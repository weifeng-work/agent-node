package main

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"strings"
)

// ---- launch.json：C1 四元组的数据化外部启动清单（v2） ----
//
// 采纳 WorkBuddy v2 审查（P0-1/P0-2/P1-1）：
//   - launch.json 是 C1 的"数据化载体"：spawn 段承载原 paths.go 内置解析逻辑的产物。
//   - **内置 C1 解析（paths.go buildLaunchSpec）保留为最终回退**，不删除——缺失/损坏/超
//     schema/老版本机器都能启动（老版本回退 = 唯一可行答案）。
//   - launch.json 由 install.ps1 生成（Write-LaunchJson：哈希表+ConvertTo-Json 构造，
//     原子写 .tmp→Move 再改名 + 保留 .bak），exe 只消费、不生成、不下载（D6 单一维护点）。
//   - 每次 spawn 现读（M-2），不缓存到 launcher 生命周期，保证节点更新后立即生效。
//   - 损坏防护：JSON 解析失败→不进安装分支、绝不自动删文件，走回退链
//     launch.json → launch.json.bak → 内置逻辑。
//   - 路径支持 {ROOT}/{DATA}/{VENV} 模板替换（每用户 %LOCALAPPDATA% 不同，F1 多账户）。

// 本 exe 支持的 schema 版本区间（整数，不做 semver 兼容矩阵）。
const (
	launchSchemaMin = 1
	launchSchemaMax = 1
)

// launcherVersion 由构建时注入（-ldflags "-X main.launcherVersion=x"）；未注入则忽略 min_launcher 检查。
var launcherVersion = "dev"

// launchJSON 对应 data/launch.json 的 schema。字段只增不改、语义稳定。
type launchJSON struct {
	SchemaVersion  int           `json:"schema_version"` // 必需
	MinLauncher    string        `json:"min_launcher"`   // 能力级变更时抬高，通知 exe"你过旧了"
	InstallCheck   []string      `json:"install_check"`  // 完整性检查路径（模板化），缺省用内置布局
	Spawn          spawnSection  `json:"spawn"`          // = C1 四元组
	Health         healthSection `json:"health"`
	ReadyTimeoutMS int           `json:"ready_timeout_ms"` // 可选，覆盖就绪超时（默认 40000）
}

type spawnSection struct {
	Exe  string            `json:"exe"`
	Args []string          `json:"args"`
	Cwd  string            `json:"cwd"`
	Env  map[string]string `json:"env"`
}

type healthSection struct {
	Endpoint string `json:"endpoint"` // 仅路径后缀（如 /api/overview），base 继续读 panel.url（P1-2，杜绝第二端口源）
}

func launchJSONPath(r string) string    { return filepath.Join(r, "data", "launch.json") }
func launchJSONBakPath(r string) string { return filepath.Join(r, "data", "launch.json.bak") }

var (
	errLaunchMissing = errors.New("launch.json missing")
	errLaunchBad     = errors.New("launch.json corrupt or schema unsupported")
)

// expandLaunch 替换 {ROOT}/{DATA}/{VENV} 模板变量（每用户路径可移植，1.2-③）。
func expandLaunch(s, root, data, venv string) string {
	return strings.NewReplacer(
		"{ROOT}", root,
		"{DATA}", data,
		"{VENV}", venv,
	).Replace(s)
}

// loadLaunchJSON 读取并校验 launch.json；失败时回退 .bak。
// 返回 errLaunchMissing（两文件都不存在，应立即回退内置，非损坏）或 errLaunchBad（损坏/schema 不支持）。
func loadLaunchJSON(r string) (*launchJSON, error) {
	if j, err := tryReadLaunch(launchJSONPath(r)); err == nil {
		return j, nil
	}
	if j, err := tryReadLaunch(launchJSONBakPath(r)); err == nil {
		logL(r, "launch.json: main corrupt, using .bak")
		return j, nil
	}
	mainErr := tryReadLaunchErr(launchJSONPath(r))
	switch {
	case errors.Is(mainErr, os.ErrNotExist):
		return nil, errLaunchMissing
	default:
		return nil, errLaunchBad
	}
}

func tryReadLaunchErr(p string) error {
	j, err := parseLaunch(p)
	if err != nil {
		return err
	}
	return validateLaunch(j)
}

// parseLaunch 读取并 JSON 解码（不校验语义），供 tryReadLaunchErr 复用错误链。
func parseLaunch(p string) (*launchJSON, error) {
	b, err := os.ReadFile(p)
	if err != nil {
		return nil, err
	}
	var j launchJSON
	if err := json.Unmarshal(b, &j); err != nil {
		return nil, errLaunchBad
	}
	return &j, nil
}

// validateLaunch 校验 launch.json 语义（schema 区间、spawn 必填、endpoint 为路径后缀）。
// P1-2：endpoint 空则默认 /api/overview；带空格或非 / 开头（如裸 host、完整 URL）一律拒绝，杜绝"第二端口源"。
func validateLaunch(j *launchJSON) error {
	if j.SchemaVersion < launchSchemaMin || j.SchemaVersion > launchSchemaMax {
		return errLaunchBad
	}
	sp := j.Spawn
	if strings.TrimSpace(sp.Exe) == "" || strings.TrimSpace(sp.Cwd) == "" {
		return errLaunchBad
	}
	switch ep := strings.TrimSpace(j.Health.Endpoint); {
	case ep == "":
		j.Health.Endpoint = "/api/overview" // 默认与 C3 对齐
	case !strings.HasPrefix(ep, "/"):
		return errLaunchBad // 拒绝非路径后缀（host/完整 URL），保持 base 单一来自 panel.url
	default:
		j.Health.Endpoint = ep
	}
	return nil
}

func tryReadLaunch(p string) (*launchJSON, error) {
	j, err := parseLaunch(p)
	if err != nil {
		return nil, err
	}
	if err := validateLaunch(j); err != nil {
		return nil, err
	}
	return j, nil
}

// loadUsableLaunch 综合判断 launch.json 对当前 exe 是否可用：可读 + 校验通过 + min_launcher
// 不高于本 exe 才返回非 nil。min_launcher 阻断（能力级变更需换 exe）时整文件视为不可用，
// 返回 err —— 供 healthEndpoint/launchReadyTimeoutMS/installedRoot 统一回退缺省（P2-1，避免
// "spawn 用内置、健康/就绪/完整性仍读过新文件" 的语义分裂）。调用方自行回退内置逻辑即可。
func loadUsableLaunch(r string) (*launchJSON, error) {
	j, err := loadLaunchJSON(r)
	if err != nil {
		return nil, err
	}
	if err := checkMinLauncher(j.MinLauncher); err != nil {
		return nil, errLaunchBad
	}
	return j, nil
}

// buildLaunchFromJSON 把 launch.json 的 spawn 段组装成 LaunchSpec（C1 数据化）。
// 模板变量先替换，env 与既有环境合并（保留已有值，额外 env 前置）。
func buildLaunchFromJSON(j *launchJSON, r, dataDir string) (LaunchSpec, error) {
	root := filepath.Clean(r)
	data := filepath.Clean(dataDir)
	venv := filepath.Join(root, "venv")

	exe := expandLaunch(j.Spawn.Exe, root, data, venv)
	if !fileExists(exe) {
		return LaunchSpec{}, errLaunchBad
	}

	env := append([]string(nil), os.Environ()...)
	for k, v := range j.Spawn.Env {
		val := strings.TrimSpace(expandLaunch(v, root, data, venv))
		// P2-7：expand（并去空白）后为空值则跳过，避免把既有 PYTHONPATH 等变量清空导致依赖找不到
		if val == "" {
			continue
		}
		key := k + "="
		idx := -1
		for i, e := range env {
			if strings.HasPrefix(e, key) {
				idx = i
				break
			}
		}
		if idx >= 0 {
			// 保留已有值：新值前置，已存在则拼接（同 buildLaunchSpec 的 PYTHONPATH 语义）
			if cur := env[idx][len(key):]; cur != "" {
				env[idx] = key + val + string(os.PathListSeparator) + cur
			} else {
				env[idx] = key + val
			}
		} else {
			env = append(env, key+val)
		}
	}

	args := make([]string, 0, len(j.Spawn.Args))
	for _, a := range j.Spawn.Args {
		args = append(args, expandLaunch(a, root, data, venv))
	}
	cwd := expandLaunch(j.Spawn.Cwd, root, data, venv)
	return LaunchSpec{Exe: exe, Args: args, Cwd: cwd, Env: env}, nil
}

// resolveLaunchSpec 本次 spawn 的规约来源解析（每次现读，M-2）。
// 优先 launch.json；缺失/损坏/schema 超界 → 回退内置 buildLaunchSpec（P0-1 保留回退）。
// 返回规约与使用来源（launch / builtin），供日志。
func resolveLaunchSpec(r, dataDir string) (LaunchSpec, string, error) {
	if j, err := loadLaunchJSON(r); err == nil {
		logL(r, "launch.json schema=%d present; building spawn spec", j.SchemaVersion)
		spec, err := buildLaunchFromJSON(j, r, dataDir)
		if err == nil {
			if err := checkMinLauncher(j.MinLauncher); err != nil {
				logL(r, "launch.json too new; fallback to builtin (%v)", err)
			} else {
				return spec, "launch", nil
			}
		} else {
			logL(r, "launch.json spawn unusable (%v); fallback to builtin", err)
		}
	}
	logL(r, "using builtin C1 launcher spec (launch.json absent/corrupt)")
	spec, err := buildLaunchInbuilt(r, dataDir)
	if err != nil {
		return LaunchSpec{}, "builtin", err
	}
	return spec, "builtin", nil
}

// checkMinLauncher 若 launch.json 声明 min_launcher 高于本 exe，则视为"能力级变更需换 exe"。
// 返回 nil 表示无需阻断；非 nil 时调用方应回退内置逻辑并提示（P2-1，方向由数据通知 exe）。
func checkMinLauncher(min string) error {
	if strings.TrimSpace(min) == "" || strings.TrimSpace(launcherVersion) == "dev" {
		return nil
	}
	cur := versionInts(launcherVersion)
	req := versionInts(min)
	for i := 0; i < 3; i++ {
		if cur[i] < req[i] {
			return errors.New("launcher too old: need " + min)
		}
		if cur[i] > req[i] {
			return nil
		}
	}
	return nil
}

// versionInts 把 x.y.z 解析为整数三元组，缺省位补 0；预发布/非数字段按 0 计。
func versionInts(v string) [3]int {
	var out [3]int
	parts := strings.Split(strings.TrimPrefix(strings.TrimSpace(v), "v"), ".")
	for i := 0; i < 3 && i < len(parts); i++ {
		// 解析纯数字前缀（digit-run），忽略 pre 后缀
		end := 0
		for end < len(parts[i]) && parts[i][end] >= '0' && parts[i][end] <= '9' {
			end++
		}
		if end > 0 {
			for c := 0; c < end; c++ {
				out[i] = out[i]*10 + int(parts[i][c]-'0')
			}
		}
	}
	return out
}

// launchReadyTimeoutMS 返回就绪超时毫秒：launch.json 可用（含 min_launcher 未阻断）时取之，
// 否则缺省 40000（M-4 与 booting 判定联动；P2-1 整文件不可用统一缺省）。
func launchReadyTimeoutMS(r string) int {
	j, err := loadUsableLaunch(r)
	if err != nil || j.ReadyTimeoutMS <= 0 {
		return 40000
	}
	return j.ReadyTimeoutMS
}
