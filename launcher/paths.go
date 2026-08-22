package main

import (
	"bufio"
	"os"
	"path/filepath"
	"strings"
)

// root 固定为 %LOCALAPPDATA%\agent-node（D1 固定家）
func root() string {
	la := os.Getenv("LOCALAPPDATA")
	if la == "" {
		la = filepath.Join(os.Getenv("USERPROFILE"), "AppData", "Local")
	}
	return filepath.Join(la, "agent-node")
}

// ---- C1 启动命令四元组解析（对齐 scripts/agent-node.ps1 Resolve-NodeLauncher） ----

// LaunchSpec 是 C1 契约的四元组。
type LaunchSpec struct {
	Exe  string   // pythonw.exe 绝对路径（base 解释器或兜底）
	Args []string // -m node.main --data-dir <DATA>
	Cwd  string   // <ROOT>\app
	Env  []string // PYTHONPATH=<ROOT>\venv\Lib\site-packages
}

// readPyvenvCfg 解析 <ROOT>\venv\pyvenv.cfg，返回 key->value（BOM/大小写不敏感/忽略 #）。
func readPyvenvCfg(venv string) map[string]string {
	m := map[string]string{}
	f, err := os.Open(filepath.Join(venv, "pyvenv.cfg"))
	if err != nil {
		return m
	}
	defer f.Close()
	s := bufio.NewScanner(f)
	for s.Scan() {
		line := strings.TrimSpace(s.Text())
		// P2-15: strip BOM（部分工具写入 pyvenv.cfg 会带 \uFEFF，导致首 key 解析失败）
		line = strings.TrimPrefix(line, "\uFEFF")
		if line == "" || strings.HasPrefix(line, "#") {
			continue
		}
		if i := strings.Index(line, "="); i >= 0 {
			key := strings.ToLower(strings.TrimSpace(line[:i]))
			val := strings.TrimSpace(line[i+1:])
			m[key] = val
		}
	}
	return m
}

// resolveLauncher 依据 pyvenv.cfg 三级回退解析出合适的 pythonw.exe（C1 exe 与 env）。
func resolveLauncher(r string) (string, string, string) {
	app := filepath.Join(r, "app")
	venv := filepath.Join(r, "venv")
	sitePkg := filepath.Join(venv, "Lib", "site-packages")

	cfg := readPyvenvCfg(venv)

	// ① home -> <home>\pythonw.exe（base 解释器直启，避免 venv 转发器双进程）
	if home, ok := cfg["home"]; ok {
		cand := filepath.Join(home, "pythonw.exe")
		if fileExists(cand) {
			// P2-9: M7 要求解析结果必须记日志
			logL(r, "launcher: pyvenv.cfg home=%s site-packages=%s", home, sitePkg)
			return cand, app, sitePkg
		}
		// 有的 home 含 python.exe，但其同目录 pythonw.exe 才是我们要的
	}
	// ② PATH 找 python -> 同目录 pythonw.exe
	if p := findPythonwInPath(); p != "" {
		logL(r, "launcher: PATH fallback exe=%s site-packages=%s", p, sitePkg)
		return p, app, sitePkg
	}
	// ③ 兜底 venv\Scripts\pythonw.exe
	fallback := filepath.Join(venv, "Scripts", "pythonw.exe")
	logL(r, "launcher: fallback exe=%s site-packages=%s", fallback, sitePkg)
	return fallback, app, sitePkg
}

func findPythonwInPath() string {
	paths := strings.Split(os.Getenv("PATH"), string(os.PathListSeparator))
	for _, dir := range paths {
		dir = strings.Trim(dir, `"`)
		if dir == "" {
			continue
		}
		// 只找可执行目录里的 python*，再补 pythonw.exe
		for _, name := range []string{"python.exe", "pythonw.exe"} {
			cand := filepath.Join(dir, name)
			if strings.EqualFold(name, "python.exe") && fileExists(cand) {
				w := filepath.Join(dir, "pythonw.exe")
				if fileExists(w) {
					return w
				}
				return cand
			}
			if strings.EqualFold(name, "pythonw.exe") && fileExists(cand) {
				return cand
			}
		}
	}
	return ""
}

func fileExists(p string) bool {
	_, err := os.Stat(p)
	return err == nil && !os.IsNotExist(err)
}

// buildLaunchSpec 组装 C1 四元组。
// shouldAutoSpawn 由上层依据持有/监督逻辑决定；这里仅生成规约。
func buildLaunchSpec(r, dataDir string) (LaunchSpec, error) {
	exe, app, sitePkg := resolveLauncher(r)
	if !fileExists(exe) {
		return LaunchSpec{}, os.ErrNotExist
	}
	env := append([]string(nil), os.Environ()...)
	if sitePkg != "" {
		// P2-6: PYTHONPATH 前置保留——若已存在则合并（前置 sitePkg），避免 os/exec 后值覆盖丢原有值
		key := "PYTHONPATH="
		merged := sitePkg
		idx := -1
		for i, e := range env {
			if strings.HasPrefix(e, key) {
				idx = i
				break
			}
		}
		if idx >= 0 {
			if cur := env[idx][len(key):]; cur != "" {
				merged = sitePkg + string(os.PathListSeparator) + cur
			}
			env[idx] = key + merged
		} else {
			env = append(env, key+merged)
		}
	}
	args := []string{"-m", "node.main", "--data-dir", dataDir}
	return LaunchSpec{Exe: exe, Args: args, Cwd: app, Env: env}, nil
}

// installedRoot 检查 %LOCALAPPDATA%\agent-node 是否"已装且完整"。
// 返回: ok(完整性), 缺失部分描述
func installedRoot(r string) (bool, string) {
	missing := []string{}
	for _, d := range []string{filepath.Join(r, "app"), filepath.Join(r, "venv"), filepath.Join(r, "data")} {
		if !isDir(d) {
			missing = append(missing, d)
		}
	}
	if _, err := os.Stat(filepath.Join(r, "venv", "Scripts", "pythonw.exe")); err != nil {
		missing = append(missing, filepath.Join(r, "venv", "Scripts", "pythonw.exe"))
	}
	if len(missing) > 0 {
		return false, strings.Join(missing, ", ")
	}
	return true, ""
}

func isDir(p string) bool {
	st, err := os.Stat(p)
	return err == nil && st.IsDir()
}