package main

import (
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"time"
)

// ---- 更新检查（v2 / D3）：托盘"检查更新" ----
//
// 设计（用户方案）：
//   - 远端版本来源 = GitHub Release 最新 tag（api.github.com .../releases/latest -> tag_name）。
//   - 本地版本来源 = 已安装节点 {ROOT}\app\node\version.py 的 VERSION 常量（节点版本，非启动器自身）。
//   - 只提示、不自动下载（D3）：检测到远端更高时弹 MessageBox 询问，确认后才跑安装脚本(runInstallScript)。
//   - 无网络/失败/相等：给明确反馈，不让用户误以为"卡住"。

const (
	// repoLatestRelease 查询仓库最新 Release 的 API（公开仓库无需鉴权）。
	repoLatestRelease = "https://api.github.com/repos/weifeng-work/agent-node/releases/latest"
	updateTimeout     = 10 * time.Second
)

// localNodeVersion 从已安装节点读版本（{ROOT}\app\node\version.py 的 VERSION 常量）。
// 读不到（未安装/解析失败）返回 ""。仅读取部署常量，不执行 get_version()（避免 git 调用）。
func localNodeVersion(r string) string {
	b, err := os.ReadFile(filepath.Join(r, "app", "node", "version.py"))
	if err != nil {
		return ""
	}
	re := regexp.MustCompile(`(?m)^VERSION\s*=\s*"([^"]+)"`)
	m := re.FindSubmatch(b)
	if len(m) < 2 {
		return ""
	}
	v := strings.TrimSpace(string(m[1]))
	return strings.TrimPrefix(v, "v")
}

// latestReleaseTag 查询 GitHub 最新 Release 的 tag（如 "0.2.4"）。失败返回错误。
type ghRelease struct {
	TagName string `json:"tag_name"`
}

// latestReleaseTagFunc 可注入（单测替换），默认走真实 API。
var latestReleaseTagFunc = latestReleaseTag

func latestReleaseTag() (string, error) {
	client := &http.Client{Timeout: updateTimeout}
	resp, err := client.Get(repoLatestRelease)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return "", fmt.Errorf("GitHub API http %d", resp.StatusCode)
	}
	var rel ghRelease
	if err := json.NewDecoder(resp.Body).Decode(&rel); err != nil {
		return "", err
	}
	return strings.TrimPrefix(strings.TrimSpace(rel.TagName), "v"), nil
}

// checkUpdate 返回 (远端版本, 本地版本, proceed, 人类可读 Message)。
// proceed=true 表示应进入"确认→下载"流程：
//   - 本地读不到（未安装 / app 损坏 / version.py 缺失）→ 只要远端可达就置 true，
//     引导重新下载安装最新版（这是对本机"未安装/损坏就重下"诉求的直接实现）。
//   - 远端比本地新 → true。
//   - 相等 / 本地超前 / 网络失败 → false（仅提示，不发起安装）。
func checkUpdate(r string) (remote, local string, proceed bool, msg string) {
	local = localNodeVersion(r)
	if local == "" {
		// 未安装或安装已损坏：直接唤醒"重新下载安装"流程（用户诉求）。
		remote, err := latestReleaseTagFunc()
		if err != nil {
			return "", "", false, fmt.Sprintf("未检测到本地节点版本，且无法连接服务器检查最新版：%v", err)
		}
		return remote, "", true,
			fmt.Sprintf("检测到本地节点未安装或文件缺失，将重新下载最新版本 v%s，是否继续？", remote)
	}
	remote, err := latestReleaseTagFunc()
	if err != nil {
		return "", local, false, fmt.Sprintf("检查失败：%v", err)
	}
	cmp := compareVersions(remote, local)
	switch {
	case cmp > 0:
		return remote, local, true, fmt.Sprintf("发现新版本 v%s（当前 v%s），是否立即更新？", remote, local)
	case cmp == 0:
		return remote, local, false, fmt.Sprintf("已是最新版本 v%s。", local)
	default:
		return remote, local, false, fmt.Sprintf("本地 v%s 高于远端 v%s（可能为开发版/超前 tag）。", local, remote)
	}
}

// compareVersions 比较 x.y.z，a>b 返回正，a<b 返负，相等返 0（复用 versionInts）。
func compareVersions(a, b string) int {
	ai, bi := versionInts(a), versionInts(b)
	for i := 0; i < 3; i++ {
		if ai[i] != bi[i] {
			if ai[i] > bi[i] {
				return 1
			}
			return -1
		}
	}
	return 0
}
