package main

import (
	"os/exec"
	"syscall"

	"golang.org/x/sys/windows"
)

// openProcessQuery 以最小查询+同步权限打开进程句柄。
func openProcessQuery(pid int) (windows.Handle, error) {
	return windows.OpenProcess(windows.PROCESS_QUERY_LIMITED_INFORMATION|windows.SYNCHRONIZE, false, uint32(pid))
}

func closeHandle(h windows.Handle) { _ = windows.CloseHandle(h) }

// processHasExited 判断进程是否已退出：WaitForSingleObject(0) 返回 WAIT_TIMEOUT -> 还活着。
func processHasExited(h windows.Handle) bool {
	ev, err := windows.WaitForSingleObject(h, 0)
	if err != nil {
		return true // 无法等待 -> 保守视为已退出
	}
	// WAIT_TIMEOUT (258) = 仍 running；WAIT_OBJECT_0 (0) = 已信号（进程结束）
	return ev != uint32(windows.WAIT_TIMEOUT)
}

// execCommand 构造隐藏窗口的辅助命令（taskkill 等）。
func execCommand(name string, args ...string) *exec.Cmd {
	c := exec.Command(name, args...)
	c.SysProcAttr = &syscall.SysProcAttr{
		HideWindow:    true,
		CreationFlags: windows.CREATE_NO_WINDOW,
	}
	return c
}

// acquireLauncherMutex 建立启动器自身互斥量（名字加 Launcher 前缀与节点区分，M9）。
// 返回 false = 已有另一实例在运行。用 CreateMutex 返回的 err 判断已存在，避免 GetLastError 顺序歧义（P2-10）。
func acquireLauncherMutex(dataDir string) (windows.Handle, bool) {
	name, err := windows.UTF16PtrFromString("AgentNodeLauncher_" + shortSHA1(dataDir))
	if err != nil {
		return 0, false
	}
	h, cerr := windows.CreateMutex(nil, true, name)
	if h == 0 {
		return 0, false
	}
	if cerr == windows.ERROR_ALREADY_EXISTS {
		_ = windows.CloseHandle(h)
		return 0, false
	}
	return h, true
}

func releaseMutex(h windows.Handle) {
	if h != 0 {
		_ = windows.CloseHandle(h)
	}
}