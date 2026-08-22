package main

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// ---- 工具：构造临时 ROOT 布局 ----

// newTestRoot 构造 {root}/venv、{root}/data，并可选生成 spawn exe。
// 返回 root 与 dataDir（= root/data），供 launch.json 测试隔离。
func newTestRoot(t *testing.T, exe bool) (string, string) {
	t.Helper()
	root := t.TempDir()
	if err := os.MkdirAll(filepath.Join(root, "venv", "Scripts"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(root, "data"), 0o755); err != nil {
		t.Fatal(err)
	}
	if exe {
		// 让 fileExists(exe) 通过：spawn.exe 随便建一个
		if err := os.WriteFile(filepath.Join(root, "venv", "Scripts", "pythonw.exe"),
			[]byte("#fake"), 0o644); err != nil {
			t.Fatal(err)
		}
	}
	return root, filepath.Join(root, "data")
}

// writeLaunch 原子写 launch.json（测试内部直接用 WriteFile 即可）。
func writeLaunch(t *testing.T, dataDir, content string) {
	t.Helper()
	if err := os.WriteFile(filepath.Join(dataDir, "launch.json"),
		[]byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
}

const validLaunchTmpl = `{
  "schema_version": 1,
  "min_launcher": "0.0.0",
  "install_check": ["{ROOT}/app", "{VENV}"],
  "spawn": {
    "exe": "{VENV}/Scripts/pythonw.exe",
    "args": ["-m", "node.main", "--data-dir", "{DATA}"],
    "cwd": "{ROOT}/app",
    "env": {"PYTHONPATH": "{VENV}/Lib/site-packages"}
  },
  "health": {"endpoint": "/api/overview"},
  "ready_timeout_ms": 40000
}`

// ---- validateLaunch ----

func TestValidateLaunch_schemaRange(t *testing.T) {
	j := &launchJSON{SchemaVersion: 2}
	if err := validateLaunch(j); err == nil {
		t.Fatal("schema=2 (> max) should be rejected")
	}
	j = &launchJSON{SchemaVersion: 0}
	if err := validateLaunch(j); err == nil {
		t.Fatal("schema=0 (< min) should be rejected")
	}
}

func TestValidateLaunch_requiresSpawn(t *testing.T) {
	j := &launchJSON{
		SchemaVersion: 1,
		Spawn:         spawnSection{Exe: " ", Cwd: " "}, // 空白视为缺失
	}
	if err := validateLaunch(j); err == nil {
		t.Fatal("empty exe+cwd should be rejected")
	}
}

func TestValidateLaunch_endpointContractP12(t *testing.T) {
	sp := spawnSection{Exe: "e", Cwd: "c"}
	// 空 endpoint -> 默认 /api/overview
	j := &launchJSON{SchemaVersion: 1, Spawn: sp}
	if err := validateLaunch(j); err != nil {
		t.Fatal(err)
	}
	if j.Health.Endpoint != "/api/overview" {
		t.Fatalf("default endpoint=%q, want /api/overview (align C3)", j.Health.Endpoint)
	}
	// 非 / 开头（裸 host/完整 URL）-> 拒绝，杜绝"第二端口源"
	for _, bad := range []string{"http://127.0.0.1:8000/api/overview", "127.0.0.1:8000", " api/overview"} {
		j = &launchJSON{SchemaVersion: 1, Spawn: sp, Health: healthSection{Endpoint: bad}}
		if err := validateLaunch(j); err == nil {
			t.Fatalf("endpoint %q should be rejected (must be path suffix)", bad)
		}
	}
	// 合法路径后缀 -> 保留
	j = &launchJSON{SchemaVersion: 1, Spawn: sp, Health: healthSection{Endpoint: "/api/health"}}
	if err := validateLaunch(j); err != nil {
		t.Fatal(err)
	}
}

// ---- versionInts / checkMinLauncher ----

func TestVersionInts(t *testing.T) {
	cases := []struct {
		in   string
		want [3]int
	}{
		{"0.1.9", [3]int{0, 1, 9}},
		{"v2.0.1", [3]int{2, 0, 1}},
		{"1.2", [3]int{1, 2, 0}},          // 缺位补 0
		{"3", [3]int{3, 0, 0}},            // 单段
		{"1.10.0-beta", [3]int{1, 10, 0}}, // pre 后缀忽略
		{"  ", [3]int{0, 0, 0}},
	}
	for _, c := range cases {
		if got := versionInts(c.in); got != c.want {
			t.Fatalf("versionInts(%q)=%v, want %v", c.in, got, c.want)
		}
	}
}

func TestCheckMinLauncher(t *testing.T) {
	orig := launcherVersion
	defer func() { launcherVersion = orig }()

	launcherVersion = "0.2.4"
	if err := checkMinLauncher("0.2.4"); err != nil {
		t.Fatalf("equal version should pass: %v", err)
	}
	if err := checkMinLauncher("0.1.0"); err != nil {
		t.Fatalf("older required should pass: %v", err)
	}
	if err := checkMinLauncher("1.0.0"); err == nil {
		t.Fatal("newer required should block (fallback builtin)")
	}
	// dev 或空 min 不阻断
	launcherVersion = "dev"
	if err := checkMinLauncher("9.9.9"); err != nil {
		t.Fatalf("dev launcher should bypass min check: %v", err)
	}
}

// ---- expandLaunch ----

func TestExpandLaunch(t *testing.T) {
	got := expandLaunch("{ROOT}/app/{DATA}/x/{VENV}/y", "R", "D", "V")
	want := "R/app/D/x/V/y"
	if got != want {
		t.Fatalf("expandLaunch=%q, want %q", got, want)
	}
}

// ---- buildLaunchFromJSON env 合并 ----

func TestBuildLaunchFromJSON_envPreservationP27(t *testing.T) {
	root, dataDir := newTestRoot(t, true)
	writeLaunch(t, dataDir, validLaunchTmpl)
	j, err := loadLaunchJSON(root)
	if err != nil {
		t.Fatal(err)
	}
	// 预置既有 PYTHONPATH，验证"额外 env 前置、既有保留"
	t.Setenv("PYTHONPATH", "/existing/path")
	spec, err := buildLaunchFromJSON(j, root, dataDir)
	if err != nil {
		t.Fatal(err)
	}
	envStr := strings.Join(spec.Env, "\n")
	// 应包含 site-packages 前置（模板 {VENV} 展开 + 拼接，分隔符可能混用，按关键字断言）+ 既有值保留
	if !strings.Contains(envStr, "site-packages") {
		t.Fatalf("PYTHONPATH missing site-packages prepend: %v", spec.Env)
	}
	if !strings.Contains(envStr, "/existing/path") {
		t.Fatalf("PYTHONPATH lost existing value: %v", spec.Env)
	}

	// 空值 env 跳过（P2-7）：不覆盖既有变量、不新增空键
	j.Spawn.Env = map[string]string{"PYTHONPATH": "  ", "DUMMY_EMPTY": ""}
	spec, err = buildLaunchFromJSON(j, root, dataDir)
	if err != nil {
		t.Fatal(err)
	}
	for _, e := range spec.Env {
		if strings.HasPrefix(e, "DUMMY_EMPTY=") {
			t.Fatalf("empty env key should be skipped: %v", spec.Env)
		}
	}
	envStr = strings.Join(spec.Env, "\n")
	if strings.Contains(envStr, "PYTHONPATH=  ") {
		t.Fatalf("whitespace env should not override PYTHONPATH: %v", spec.Env)
	}
}

// ---- 回退链：loadLaunchJSON / resolveLaunchSpec ----

func TestLoadLaunchJSON_fallbackChain(t *testing.T) {
	root, dataDir := newTestRoot(t, false)

	// 两边都不存在 -> errLaunchMissing（回退内置，非损坏）
	if _, err := loadLaunchJSON(root); err != errLaunchMissing {
		t.Fatalf("missing both -> want errLaunchMissing, got %v", err)
	}

	// main 损坏、.bak 有效 -> 用 .bak
	writeLaunch(t, dataDir, "not-json{{{")
	os.WriteFile(filepath.Join(dataDir, "launch.json.bak"),
		[]byte(strings.Replace(validLaunchTmpl, "{ROOT}/app", "{ROOT}/app", 1)), 0o644)
	// 这里 exe 不存在/未校验到，.bak 仅测试回退拿取（解析成功即可）
	if _, err := loadLaunchJSON(root); err != nil {
		t.Fatalf("want .bak fallback ok, got %v", err)
	}

	// 两者都损坏 -> errLaunchBad
	writeLaunch(t, dataDir, "not-json")
	os.WriteFile(filepath.Join(dataDir, "launch.json.bak"),
		[]byte("also-bad"), 0o644)
	if _, err := loadLaunchJSON(root); err != errLaunchBad {
		t.Fatalf("both corrupt -> want errLaunchBad, got %v", err)
	}
}

func TestResolveLaunchSpec_builtinFallback(t *testing.T) {
	root, dataDir := newTestRoot(t, false)

	// 无 launch.json -> builtin（本机 PATH 存在 pythonw.exe 时 builtin 能成功；不依赖其具体成功/失败）
	spec, src, err := resolveLaunchSpec(root, dataDir)
	if src != "builtin" {
		t.Fatalf("src=%q, want builtin", src)
	}
	_ = spec
	_ = err

	// 有 launch.json 且 schema 超界 -> 回退内置
	writeLaunch(t, dataDir, `{"schema_version": 99}`)
	if _, src, _ := resolveLaunchSpec(root, dataDir); src != "builtin" {
		t.Fatalf("schema超界 应回退 builtin, got %q", src)
	}

	// 有 launch.json 且 min_launcher 高于本 exe -> 回退内置
	launcherVersion = "0.0.0"
	defer func() { launcherVersion = "dev" }()
	writeLaunch(t, dataDir, `{
  "schema_version": 1,
  "min_launcher": "9.9.9",
  "spawn": {"exe": "{VENV}/Scripts/pythonw.exe", "args": ["-m","node.main"], "cwd": "{ROOT}/app"},
  "health": {"endpoint": "/api/overview"}
}`)
	os.WriteFile(filepath.Join(root, "venv", "Scripts", "pythonw.exe"), []byte("#"), 0o644)
	_, src, _ = resolveLaunchSpec(root, dataDir)
	if src != "builtin" {
		t.Fatalf("min_launcher 过高 应回退内置, got %q", src)
	}
}
