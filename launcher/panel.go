package main

import (
	"os/exec"
	"syscall"
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

// runInstallScript 隐藏 PowerShell 执行安装脚本（D6）。（更新时的可见版见 runInstallVerbose。）
func runInstallScript() error {
	return execCommand("powershell", "-NoProfile", "-NonInteractive",
		"-WindowStyle", "Hidden", "-Command", installCmd).Run()
}

// runInstallVerbose 更新/重装用可见 PowerShell 控制台运行 install.ps1，实时显示下载/安装进度。
// install.ps1 自身会打印 Step/下载百分比/pip 进度；结尾 cmd 的 pause 停留让用户看到结果，
// 避免窗口随进程结束瞬间关闭。成功/失败都回到调用方再弹结果框。
// 必须 CREATE_NEW_CONSOLE 打开新可见窗（不能用 execCommand 的隐藏窗）。
func runInstallVerbose() error {
	inner := "powershell -NoProfile -NonInteractive -WindowStyle Normal -Command \"" +
		installCmd + "\""
	cmd := exec.Command("cmd", "/c", inner+" & pause")
	cmd.SysProcAttr = &syscall.SysProcAttr{
		CreationFlags: windows.CREATE_NEW_CONSOLE, // 开独立可见控制台窗，显示实时进度
	}
	return cmd.Run()
}

// notifyError 安装/初始化失败的用户可见报错弹窗。
func notifyError(title, text string) {
	// MB_ICONERROR(0x10)
	messageBox(title, text, 0x00000010)
}
