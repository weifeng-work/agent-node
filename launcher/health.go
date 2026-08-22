package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// ---- C4 node.lock：读取 PID 并判存活 ----

func lockPath(r string) string     { return filepath.Join(r, "data", "node.lock") }
func panelURLPath(r string) string { return filepath.Join(r, "data", "panel.url") }

// readLockPID 读 node.lock，返回 PID 与解析状态。file 不存在 -> ok=false。
func readLockPID(r string) (pid int, ok bool) {
	b, err := os.ReadFile(lockPath(r))
	if err != nil {
		return 0, false
	}
	s := strings.TrimSpace(string(b))
	if s == "" {
		return 0, false
	}
	n, err := strconv.Atoi(s)
	if err != nil {
		return 0, false
	}
	return n, true
}

// pidAlive 判断 PID 是否存活（OpenProcess 语义）。
func pidAlive(pid int) bool {
	if pid <= 0 {
		return false
	}
	// ProcessBasicInformation: OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION) 再 ExitCode/times 探活
	h, err := openProcessQuery(pid)
	if err != nil {
		return false
	}
	defer closeHandle(h)
	// 进程句柄可开且进程仍在 -> 用 WaitForSingleObject 0 判定退出。
	return !processHasExited(h)
}

// ---- C3 panel.url ----

func readPanelURL(r string) string {
	b, err := os.ReadFile(panelURLPath(r))
	if err != nil {
		return ""
	}
	return strings.TrimSpace(string(b))
}

// healthEndpoint P1-2：从 launch.json 取健康判定路径后缀（默认 /api/overview）。
// base 仍由 C3 panel.url 提供，杜绝第二个端口/URL 事实源；只外置路径后缀。
// P2-1：launch.json 不可用（缺失/损坏/min_launcher 阻断）时统一回缺省，不与 spawn 语义分裂。
func healthEndpoint(r string) string {
	if j, err := loadUsableLaunch(r); err == nil && j.Health.Endpoint != "" {
		return j.Health.Endpoint
	}
	return "/api/overview"
}

// livePanelURL M6/P2-7: 优先返回探测到的真实可用面板 URL（overview.PanelUrl），失败回退 panel.url。
func livePanelURL(r string) string {
	u := readPanelURL(r)
	if u == "" {
		return ""
	}
	if ov, err := probeOverview(u, healthEndpoint(r)); err == nil && ov.PanelUrl != "" {
		return ov.PanelUrl
	}
	return u
}

// ---- /api/overview 健康判定（含义"全栈活"） ----

type overview struct {
	NodeId   string `json:"nodeId"`
	Version  string `json:"version"`
	Pid      int    `json:"pid"`
	Status   string `json:"status"`
	PanelUrl string `json:"panelUrl"`
	Uptime   int64  `json:"uptimeSec"`
}

// probeOverview 探测 panelURL 的 health endpoint（来自 launch.json，默认 /api/overview）。
func probeOverview(panelURL, endpoint string) (*overview, error) {
	client := &http.Client{Timeout: 2 * time.Second}
	base := strings.TrimSuffix(panelURL, "/") + endpoint
	resp, err := client.Get(base)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return nil, fmt.Errorf("http %d", resp.StatusCode)
	}
	body, _ := io.ReadAll(resp.Body)
	var ov overview
	if err := json.Unmarshal(body, &ov); err != nil {
		return nil, err
	}
	// P1-2：以 overview 独有的 pid 字段做结构校验——nodeId 不足以区分（/api/health 也返回
	// nodeId），改用 pid（仅 /api/overview 返回）。若 health.endpoint 被改成非 overview（如
	// /api/health，无 pid），视为探测失败，避免 tooltip 的 version/pid/panelURL 静默为空。
	if ov.Pid <= 0 {
		return nil, fmt.Errorf("endpoint %s is not /api/overview (missing pid)", endpoint)
	}
	return &ov, nil
}

// ---- 进程树停止（M8：显式 taskkill /T /F，禁止 Job Object 连坐） ----

func taskkillTree(pid int) (string, error) {
	cmd := execCommand("taskkill", "/PID", strconv.Itoa(pid), "/T", "/F")
	out, err := cmd.CombinedOutput()
	return string(out), err
}
