package main

import (
	"os"
	"path/filepath"
	"testing"
)

// ---- compareVersions ----
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
