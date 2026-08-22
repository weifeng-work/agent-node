package main

import (
	"errors"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

// ---- checkUpdate：损坏/未装唤醒"重新下载"（注入 latestReleaseTagFunc 保证确定、不依赖网络） ----
func TestCheckUpdate_damagedLocalBranch(t *testing.T) {
	saved := latestReleaseTagFunc
	latestReleaseTagFunc = func() (string, error) { return "0.2.6", nil }
	defer func() { latestReleaseTagFunc = saved }()

	root := t.TempDir() // 空 ROOT：app\node\version.py 一定缺失
	remote, local, proceed, msg := checkUpdate(root)
	if local != "" {
		t.Fatalf("empty ROOT should yield local==\"\", got %q", local)
	}
	if !proceed {
		t.Fatalf("local empty but remote reachable should proceed, got proceed=%v msg=%q", proceed, msg)
	}
	if remote != "0.2.6" {
		t.Fatalf("remote=%q, want 0.2.6", remote)
	}
	if !strings.Contains(msg, "重新下载") {
		t.Fatalf("proceed with empty local should say 重新下载, got %q", msg)
	}
}

func TestCheckUpdate_damagedAndNoNet(t *testing.T) {
	saved := latestReleaseTagFunc
	latestReleaseTagFunc = func() (string, error) { return "", errors.New("net down") }
	defer func() { latestReleaseTagFunc = saved }()

	root := t.TempDir()
	_, _, proceed, msg := checkUpdate(root)
	if proceed {
		t.Fatalf("no-net damaged should not proceed, got msg=%q", msg)
	}
	if !strings.Contains(msg, "无法连接服务器") {
		t.Fatalf("no-net damaged should report network failure, got %q", msg)
	}
}

func TestCompareVersions(t *testing.T) {
	cases := []struct {
		a, b string
		want int
	}{
		{"0.2.4", "0.2.3", 1},
		{"0.2.3", "0.2.4", -1},
		{"0.2.4", "0.2.4", 0},
		{"v0.2.4", "0.2.3", 1}, // 前导 v 已剥离（latestReleaseTag/localNodeVersion 均剥）
		{"1.0.0", "0.9.9", 1},
		{"0.2.10", "0.2.9", 1}, // 十位进位不能按字符串
		{"0.2.3", "0.2.3-beta", 0},
	}
	for _, c := range cases {
		if got := compareVersions(c.a, c.b); got != c.want {
			t.Fatalf("compareVersions(%q,%q)=%d, want %d", c.a, c.b, got, c.want)
		}
	}
}

// ---- localNodeVersion ----
func TestLocalNodeVersion(t *testing.T) {
	root := t.TempDir()
	dir := filepath.Join(root, "app", "node")
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}

	// 缺失文件 -> ""
	if v := localNodeVersion(root); v != "" {
		t.Fatalf("missing version.py -> want \"\", got %q", v)
	}

	// 正常部署形态（bump 后 VERSION 常量为 tag 去 v）
	content := `"""doc"""
VERSION = "0.2.4"

def _git_describe(): ...
VERSION = get_version()
`
	if err := os.WriteFile(filepath.Join(dir, "version.py"), []byte(content), 0o644); err != nil {
		t.Fatal(err)
	}
	if v := localNodeVersion(root); v != "0.2.4" {
		t.Fatalf("want local=0.2.4, got %q", v)
	}
}
