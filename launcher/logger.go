package main

import (
	"fmt"
	"os"
	"path/filepath"
	"time"
)

// logPath 启动器自身日志（M7）：data/launcher.log
func launcherLogPath(r string) string { return filepath.Join(r, "data", "launcher.log") }
func stderrLogPath(r string) string   { return filepath.Join(r, "data", "launcher-stderr.log") }

// logL 写日志（追加 + 轻微轮转到 10MB）。
func logL(r string, format string, args ...interface{}) {
	line := fmt.Sprintf("%s  %s\n", time.Now().Format("2006-01-02T15:04:05"), fmt.Sprintf(format, args...))
	p := launcherLogPath(r)
	if st, err := os.Stat(p); err == nil && st.Size() > 10*1024*1024 {
		_ = os.Rename(p, p+".old") // 简单轮转
	}
	f, err := os.OpenFile(p, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	defer f.Close()
	_, _ = f.WriteString(line)
}

// openStderrLog 打开/截断 stderr 重定向文件，返回用于 CreateProcess 的写句柄路径。
func openStderrLog(r string) (*os.File, error) {
	p := stderrLogPath(r)
	f, err := os.OpenFile(p, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o644)
	if err != nil {
		return nil, err
	}
	return f, nil
}
