package main

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"time"

	"github.com/getlantern/systray"
)

func main() {
	r := root()
	dataDir := filepath.Join(r, "data")
	_ = os.MkdirAll(dataDir, 0o755)

	// M9: 启动器自身单实例互斥量（与节点互斥量命名区分）。
	mh, ok := acquireLauncherMutex(dataDir)
	if !ok {
		logL(r, "another launcher running; focusing panel")
		if u := livePanelURL(r); u != "" {
			openBrowser(u)
		} else if u := readPanelURL(r); u != "" {
			openBrowser(u)
		}
		return
	}
	defer releaseMutex(mh)

	logL(r, "launcher start (go %s)", runtime.Version())

	// D6: 安装引导 / 组件缺失驻留红态（P1-5）
	okRoot, missing := installedRoot(r)
	if !okRoot {
		if !isDir(r) {
			// 未安装：是/否 引导安装
			if !confirmInstall() {
				logL(r, "user declined install; exiting")
				return
			}
			logL(r, "user accepted; running install.ps1")
			if err := runInstallScript(); err != nil {
				logL(r, "install failed: %v", err)
				notifyError("安装失败",
					fmt.Sprintf("安装出错：%v\n\n请手动在 PowerShell 执行：\n%s", err, installCmd))
				return
			}
			if ok2, m2 := installedRoot(r); !ok2 {
				notifyError("安装后仍不完整", m2)
				return
			}
		} else {
			// 已装但不完整：驻留托盘红态 + tooltip"组件缺失"，不退出（P1-5）
			logL(r, "install incomplete (tray red): %s", missing)
			w := newWatchState(r, dataDir)
			w.incomplete = missing
			go w.run() // P2-新1: 跑 watcher 才能更新 tooltip 原因并消费菜单命令（incomplete 时 reconcile 不 spawn）
			systray.Run(func() { onTrayReady(w) }, func() { onTrayExit(w) })
			return
		}
	}

	w := newWatchState(r, dataDir)
	go w.run()
	systray.Run(
		func() { onTrayReady(w) },
		func() { onTrayExit(w) },
	)
}

// ---- 托盘（绿/红两态 + 左键开面板 + 右键菜单） ----

func onTrayReady(w *watchState) {
	systray.SetTitle("agent-node")
	systray.SetTooltip("agent-node launcher")

	mOpen := systray.AddMenuItem("打开面板", "在浏览器打开 Web 面板")
	systray.AddSeparator()
	mStart := systray.AddMenuItem("启动节点", "启动/拉起节点")
	mStop := systray.AddMenuItem("停止节点", "停止节点（需再次启动）")
	mRestart := systray.AddMenuItem("重启节点", "停止后重新拉起")
	systray.AddSeparator()
	mCheckUpdate := systray.AddMenuItem("检查更新", "检查节点是否有新版本")
	systray.AddSeparator()
	mQuit := systray.AddMenuItem("退出托盘", "退出；节点保持运行（D7）")

	// 图标与 tooltip 轮询刷新：只读原子快照，避免与 run 循环写侧数据竞争（P2-12）
	go func() {
		t := time.NewTicker(2 * time.Second)
		defer t.Stop()
		for {
			<-t.C
			s := w.loadSnapshot()
			if s.alive && s.healthy {
				systray.SetIcon(iconGreen())
			} else {
				systray.SetIcon(iconRed())
			}
			systray.SetTooltip(formatTooltip(s))
		}
	}()

	// 菜单动作投递到看门狗循环（线程安全）
	for {
		select {
		case <-mOpen.ClickedCh:
			// M6: 优先用探测到的活面板 URL，panel.url 仅作回退（P2-7）
			if u := livePanelURL(w.r); u != "" {
				openBrowser(u)
			}
		case <-mStart.ClickedCh:
			w.cmds <- func() { w.cmdStart() }
		case <-mStop.ClickedCh:
			w.cmds <- func() { w.cmdStop() }
		case <-mRestart.ClickedCh:
			w.cmds <- func() { w.cmdRestart() }
		case <-mCheckUpdate.ClickedCh:
			w.cmds <- func() { w.cmdCheckUpdate() }
		case <-mQuit.ClickedCh:
			systray.Quit()
			return
		}
	}
}

// formatTooltip 按健康快照拼人读 tooltip（§7.3 / P2-8：version/pid/面板URL/红态原因）。
func formatTooltip(s stateSnapshot) string {
	head := "agent-node"
	if s.version != "" {
		head += " v" + s.version
	}
	if s.pid > 0 {
		head += fmt.Sprintf(" (pid %d)", s.pid)
	}
	if s.panelURL != "" {
		head += "\n面板: " + s.panelURL
	}
	if s.reason != "" {
		head += "\n" + s.reason
	}
	return head
}

func onTrayExit(w *watchState) {
	// 退出托盘：通知看门狗循环结束；节点保持运行，绝不 kill（D7/M8）。
	close(w.quit)
}
