package main

import (
	"errors"
	"fmt"
	"os"
	"os/exec"
	"sync/atomic"
	"syscall"
	"time"

	"golang.org/x/sys/windows"
)

// stateSnapshot 供托盘线程只读的健康快照（消除与 run 循环写侧的数据竞争，P2-12）。
type stateSnapshot struct {
	holding    bool
	alive      bool        // node.lock PID 存活
	healthy    bool        // + /api/overview 200（全栈活，P1-1）
	tripped    bool        // 熔断已断开
	paused     bool        // 已显式停止
	incomplete string      // 组件缺失描述（非空=已装但不完整）
	booting    bool        // 已 spawn、进程活但面板未就绪
	reason     string      // 人读 tooltip 原因
	version    string
	pid        int
	panelURL   string
}

// watchState 看门狗核心状态。
type watchState struct {
	r       string
	dataDir string

	child      *exec.Cmd // 持有的子进程（holding 时有值）
	childPID   int
	holding    bool  // true=以父进程持有；false=并行监督
	paused     bool  // true=显式停止后不再自动拉起（等下一行 Start/Restart）
	incomplete string
	childDone  chan int
	cmds       chan func()
	quit       chan struct{}
	nextSpawn  time.Time
	consecFail int
	tripped    bool
	childSpawn time.Time // 最近一次 spawn 起点（就绪判定用，P1-1）

	// 熔断窗口记录（仅 run 循环访问，P1-4）
	crashes []time.Time

	// HTTP 探测节流缓存（仅 run 循环访问）
	lastProbe time.Time
	lastOv    *overview

	// 托盘线程只读快照
	snap atomic.Value // stateSnapshot
}

func newWatchState(r, dataDir string) *watchState {
	w := &watchState{
		r:         r,
		dataDir:   dataDir,
		childDone: make(chan int, 1),
		cmds:      make(chan func(), 8),
		quit:      make(chan struct{}),
	}
	w.snap.Store(stateSnapshot{reason: "启动器就绪，监测中…"})
	return w
}

func (w *watchState) loadSnapshot() stateSnapshot {
	if s, ok := w.snap.Load().(stateSnapshot); ok {
		return s
	}
	return stateSnapshot{reason: "监测中…"}
}

// ---- 存活判定（C4 + HTTP 全栈） ----

// nodeAliveFast 仅进程级存活（node.lock PID）。持续高频使用、不弹网络。
func (w *watchState) nodeAliveFast() bool {
	pid, ok := readLockPID(w.r)
	if !ok {
		return false
	}
	return pidAlive(pid)
}

// nodeHealthy 端到端健康：node.lock PID 活 + HTTP /api/overview 200（P1-1 去惰性回退）。
func (w *watchState) nodeHealthy() bool {
	if !w.nodeAliveFast() {
		return false
	}
	return w.probeOnce() != nil
}

// probeOnce 带节流(5s)的 /api/overview 探测，成功缓存 overview 供 tooltip/就绪判定复用。
func (w *watchState) probeOnce() *overview {
	if time.Since(w.lastProbe) < 5*time.Second {
		return w.lastOv
	}
	w.lastProbe = time.Now()
	u := readPanelURL(w.r)
	if u == "" {
		w.lastOv = nil
		return nil
	}
	ov, err := probeOverview(u)
	if err != nil {
		w.lastOv = nil
		return nil
	}
	w.lastOv = ov
	return ov
}

// evaluateHealth 汇总一次健康快照供托盘读取（每次 reconcile / 命令后调用）。
func (w *watchState) evaluateHealth() {
	s := stateSnapshot{
		holding:    w.holding,
		paused:     w.paused,
		tripped:    w.tripped,
		incomplete: w.incomplete,
	}
	s.alive = w.nodeAliveFast()
	if ov := w.probeOnce(); ov != nil {
		s.healthy = true
		s.version = ov.Version
		s.pid = ov.Pid
		s.panelURL = ov.PanelUrl
	}
	// 就绪判定（P1-1）：spawn 后进程活但面板未就绪，窗口内视为"启动中"
	s.booting = w.holding && s.alive && !s.healthy &&
		!w.childSpawn.IsZero() && time.Since(w.childSpawn) < 40*time.Second

	switch {
	case w.incomplete != "":
		s.reason = "组件缺失：" + w.incomplete + "，请重新安装"
	case w.tripped:
		s.reason = "已熔断：1 分钟内多次崩溃，已停自动重启，请查看 node.log"
	case !s.alive && w.paused:
		s.reason = "已停止"
	case !s.alive:
		s.reason = "未运行"
	case s.healthy && w.holding:
		s.reason = "运行中 (spawned)"
	case s.healthy:
		s.reason = "运行中 (supervised)"
	case s.booting:
		s.reason = "启动中，面板未就绪…"
	default:
		s.reason = "进程存活但面板异常(/api/overview 不可达)"
	}
	w.snap.Store(s)
}

// ---- 持有 / 监督（C2/C4） ----

// spawnHeld 以子进程方式持有启动节点（C1 四元组 + stderr 重定向）。
func (w *watchState) spawnHeld() error {
	spec, err := buildLaunchSpec(w.r, w.dataDir)
	if err != nil {
		return err
	}
	stderrF, err := openStderrLog(w.r)
	if err != nil {
		return err
	}
	cmd := exec.Command(spec.Exe, spec.Args...)
	cmd.Dir = spec.Cwd
	cmd.Env = spec.Env
	cmd.Stdout = stderrF
	cmd.Stderr = stderrF
	mkattr := func(flags uint32) *syscall.SysProcAttr {
		return &syscall.SysProcAttr{HideWindow: true, CreationFlags: flags}
	}
	// 关键：禁止 Job Object KILL_ON_JOB_CLOSE（D7/M8——退出不连坐杀节点）。
	// BREAKAWAY_FROM_JOB 尽力而为：宿主 Job 不允许 breakaway 时 Start 会 ACCESS_DENIED，降级重试（P2-11）。
	cmd.SysProcAttr = mkattr(windows.CREATE_NO_WINDOW | windows.CREATE_BREAKAWAY_FROM_JOB)
	if err := cmd.Start(); err != nil {
		if errors.Is(err, windows.ERROR_ACCESS_DENIED) {
			logL(w.r, "spawn access-denied (breakaway); retry without BREAKAWAY_FROM_JOB")
			cmd.SysProcAttr = mkattr(windows.CREATE_NO_WINDOW)
			if err2 := cmd.Start(); err2 != nil {
				_ = stderrF.Close()
				return err2
			}
		} else {
			_ = stderrF.Close()
			return err
		}
	}
	w.child = cmd
	w.childPID = cmd.Process.Pid
	w.holding = true
	w.childSpawn = time.Now()
	logL(w.r, "spawned node pid=%d (hold)", w.childPID)

	// 子进程退出码经 Wait goroutine 回传（M2 语义依赖真实 exit code）
	go func() {
		code := 0
		if err := cmd.Wait(); err != nil {
			var ee *exec.ExitError
			if errors.As(err, &ee) {
				code = ee.ExitCode()
			}
		}
		_ = stderrF.Close()
		select {
		case w.childDone <- code:
		default:
		}
	}()
	return nil
}

// syncExisting 读 node.lock：已有活 PID -> 转监督；否则（且已完整安装）尝试持有 spawn。
func (w *watchState) syncExisting() {
	w.evaluateHealth()
	if w.paused {
		return
	}
	if w.incomplete != "" {
		return
	}
	// P3-7: 启动时顺手清残留死锁，避免首次 spawn 触发核心"异常退出"模态告警
	if pl, ok := readLockPID(w.r); ok && !pidAlive(pl) {
		_ = os.Remove(lockPath(w.r))
	}
	if pid, ok := readLockPID(w.r); ok && pidAlive(pid) {
		w.childPID = pid
		w.holding = false
		w.consecFail = 0
		logL(w.r, "existing live node pid=%d -> supervisor mode", pid)
		return
	}
	if ok, m := installedRoot(w.r); !ok {
		w.incomplete = m
		w.holding = false
		w.evaluateHealth()
		logL(w.r, "installed root incomplete; supervisor-only: %s", m)
		return
	}
	if err := w.spawnHeld(); err != nil {
		logL(w.r, "spawn failed: %v", err)
		w.holding = false
	}
}

// stopNode 显式停止（M5/M8：taskkill 树；仅用户主动停止走此路径）。
func (w *watchState) stopNode() {
	pid, ok := readLockPID(w.r)
	if !ok || pid <= 0 {
		if w.child != nil {
			pid = w.childPID
		}
	}
	if pid <= 0 {
		logL(w.r, "stop: nothing to stop")
		return
	}
	logL(w.r, "stopping node pid=%d (explicit)", pid)
	_, _ = taskkillTree(pid)

	deadline := time.Now().Add(8 * time.Second)
	for time.Now().Before(deadline) {
		if !pidAlive(pid) {
			break
		}
		time.Sleep(200 * time.Millisecond)
	}
	w.child = nil
	w.childPID = 0
	w.holding = false
	// 防残留锁（M5）：PID 已死则清 node.lock，避免下次启动弹异常退出警告
	if !pidAlive(pid) {
		_ = os.Remove(lockPath(w.r))
		logL(w.r, "node stopped")
	} else {
		logL(w.r, "stop timeout: node alive; handing to reconcile")
	}
	w.evaluateHealth()
}

// ---- 退出处理（M2 + M3a 宽限期 + P1-3 退出码三分支） ----

func (w *watchState) onChildExit(code int) {
	w.child = nil
	w.holding = false

	switch code {
	case 0:
		// P1-3: exit==0 正常退出 -> 不重启、不计熔断。
		if pl, ok := readLockPID(w.r); ok && pidAlive(pl) {
			w.childPID = pl
			w.consecFail = 0
			logL(w.r, "child exit=0; lock pid=%d alive -> supervisor", pl)
			w.evaluateHealth()
			return
		}
		if pl, ok := readLockPID(w.r); ok && !pidAlive(pl) {
			_ = os.Remove(lockPath(w.r))
		}
		w.childPID = 0
		w.nextSpawn = time.Now().Add(24 * time.Hour) // 正常退出不自动重启，等用户 Start
		w.evaluateHealth()
		logL(w.r, "child exit=0 (clean); auto-restart disabled until Start")
		return

	case 1:
		// M2: exit==1 且锁仍有活 PID -> 已被他人持有 / restart_self -> 转监督，不计熔断。
		if pl, ok := readLockPID(w.r); ok && pidAlive(pl) {
			w.childPID = pl
			w.consecFail = 0
			logL(w.r, "child exit=1 but lock pid=%d alive -> supervisor (M2)", pl)
			w.evaluateHealth()
			return
		}
		// 锁已死 -> 启动失败或崩溃，走退避熔断。
		w.recordCrash("exit=1 with dead lock (start failed)")

	default:
		// 其他非 0 -> 一律按崩溃重启（真实崩溃信号，P1-3）。
		w.recordCrash(fmt.Sprintf("exit=%d crash", code))
	}
	w.evaluateHealth()
}

// recordCrash 记录一次崩溃：清残留锁、计熔断、布退避（宽限期为底，P1-2/P1-4）。
func (w *watchState) recordCrash(detail string) {
	if pl, ok := readLockPID(w.r); ok && !pidAlive(pl) {
		_ = os.Remove(lockPath(w.r)) // M5 清残留锁
	}
	w.consecFail++
	now := time.Now()
	w.crashes = append(w.crashes, now)
	keep, tripped := crashWindow(w.crashes, now, 60*time.Second, 3)
	w.crashes = keep
	if tripped {
		w.tripped = true
		w.nextSpawn = time.Time{} // 熔断：不再自动 spawn，等用户 Start / 健康恢复
		logL(w.r, "CIRCUIT BREAK: %d crashes within 60s; auto-restart halted (%s)",
			len(w.crashes), detail)
		return
	}
	// M3a 宽限期(6s)为底，退避取大（P1-2，避免 restart_self 双 spawn 争锁）
	delay := crashDelay(w.consecFail)
	w.nextSpawn = time.Now().Add(time.Duration(delay) * time.Second)
	logL(w.r, "crash: %s consecFail=%d backoff=%ds (grace=6s floor)", detail, w.consecFail, delay)
}

// crashWindow 崩溃窗口判定：只看 window 内的时间戳，>= limit 次则熔断（纯函数，供单测）。
func crashWindow(crashes []time.Time, now time.Time, window time.Duration, limit int) ([]time.Time, bool) {
	cutoff := now.Add(-window)
	keep := crashes[:0]
	for _, c := range crashes {
		if !c.Before(cutoff) {
			keep = append(keep, c)
		}
	}
	return keep, len(keep) >= limit
}

// crashDelay 根据连续失败次数计算退避秒数：1..64s 指数，6s 宽限期为底（纯函数，供单测）。
func crashDelay(consecFail int) int {
	exp := 1 << uint(min(consecFail-1, 6)) // 1..64s
	return max(6, exp)
}

// ---- 主循环 ----

func (w *watchState) run() {
	w.syncExisting()
	ticker := time.NewTicker(3 * time.Second)
	defer ticker.Stop()
	for {
		select {
		case code := <-w.childDone:
			w.onChildExit(code)
		case fn := <-w.cmds:
			fn()
			w.evaluateHealth()
		case <-ticker.C:
			w.reconcile()
		case <-w.quit:
			logL(w.r, "launcher watcher exiting; node left running (D7)")
			return
		}
	}
}

// reconcile 周期性对齐：健康清零/熔断复位、持有/监督切换、paused/退避/熔断下的拉起门控。
func (w *watchState) reconcile() {
	w.evaluateHealth()
	if w.incomplete != "" {
		return // 组件缺失：不 spawn，托盘红
	}
	// 健康（进程活 + HTTP 200）-> 清零熔断并复位熔断态
	if w.holding && w.nodeAliveFast() && w.nodeHealthy() {
		w.consecFail = 0
		if w.tripped {
			w.tripped = false
			w.crashes = w.crashes[:0]
			logL(w.r, "health restored; circuit breaker reset")
		}
		return
	}
	// 现有活 PID -> 转监督（含宽限期/退避窗口内 restart_self 的新进程接管）
	if pl, ok := readLockPID(w.r); ok && pidAlive(pl) {
		w.childPID = pl
		w.holding = false
		w.consecFail = 0
		if w.tripped {
			w.tripped = false
			w.crashes = w.crashes[:0]
		}
		return
	}
	if w.holding {
		// 仍持有但锁无活 PID：等 childDone 回传退出，不重复 spawn
		return
	}
	if w.paused || w.tripped {
		return
	}
	if time.Now().Before(w.nextSpawn) {
		return
	}
	if ok, _ := installedRoot(w.r); !ok {
		return
	}
	logL(w.r, "no live node; respawning (reconcile)")
	_ = w.spawnHeld()
}

// ---- 命令（来自托盘） ----

func (w *watchState) cmdStart() {
	w.paused = false
	w.tripped = false
	w.crashes = w.crashes[:0]
	w.consecFail = 0
	w.nextSpawn = time.Now()
	logL(w.r, "cmd: start")
}

func (w *watchState) cmdStop() {
	w.paused = true
	w.tripped = false
	w.nextSpawn = time.Time{}
	w.stopNode()
	logL(w.r, "cmd: stop")
}

func (w *watchState) cmdRestart() {
	w.paused = false
	w.tripped = false
	w.crashes = w.crashes[:0]
	w.consecFail = 0
	w.childSpawn = time.Time{}
	w.stopNode()
	w.nextSpawn = time.Now() // 停止后由 reconcile 立即重新持有
	logL(w.r, "cmd: restart")
}