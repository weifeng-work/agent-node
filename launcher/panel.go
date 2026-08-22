package main

import (
	"unsafe"

	"golang.org/x/sys/windows"
)

// installCmd 安装引导脚本（与 scripts/agent-node.ps1 / SKILL 同源 URL）。
const installURL = "https://raw.githubusercontent.com/weifeng-work/agent-node/main/scripts/install.ps1"
const installCmd = "irm " + installURL + " | iex"

// openBrowser 用默认浏览器打开 URL（rundll32 协议处理）。
func openBrowser(url string) {
	_ = execCommand("rundll32.exe", "url.dll,FileProtocolHandler", url).Start()
}

// messageBox 弹 Windows 原生 MessageBoxW，返回点击按键（IDYES=6 等）。
func messageBox(title, text string, flags uint32) uintptr {
	caption, _ := windows.UTF16PtrFromString(title)
	t, _ := windows.UTF16PtrFromString(text)
	mb := windows.NewLazySystemDLL("user32.dll").NewProc("MessageBoxW")
	r, _, _ := mb.Call(0, uintptr(unsafe.Pointer(t)), uintptr(unsafe.Pointer(caption)), uintptr(flags))
	return r
}

// confirmInstall D6：未安装时询问用户是否安装（是 -> runInstallScript；否 -> 退出）。
func confirmInstall() bool {
	// MB_YESNO(0x4) | MB_ICONQUESTION(0x20)
	r := messageBox("agent-node 启动器",
		"未检测到 %LOCALAPPDATA%\\agent-node 安装。\n\n是否现在自动安装？\n（是=后台执行安装脚本；否=退出）",
		0x00000004|0x00000020)
	return r == 6 // IDYES
}

// runInstallScript 隐藏 PowerShell 执行安装脚本（D6）。
func runInstallScript() error {
	return execCommand("powershell", "-NoProfile", "-NonInteractive",
		"-WindowStyle", "Hidden", "-Command", installCmd).Run()
}

// notifyError 安装/初始化失败的用户可见报错弹窗。
func notifyError(title, text string) {
	// MB_ICONERROR(0x10)
	messageBox(title, text, 0x00000010)
}