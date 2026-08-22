package main

import (
	"testing"
	"time"
)

func TestCrashDelay_graceFloor(t *testing.T) {
	// M3a 宽限期(6s)为底：即使首崩退避 1s 也被抬到 6s（P1-2）
	if d := crashDelay(1); d != 6 {
		t.Fatalf("crashDelay(1)=%d, want 6 (grace floor)", d)
	}
	if d := crashDelay(2); d != 6 {
		t.Fatalf("crashDelay(2)=%d, want 6 (grace floor)", d)
	}
}

func TestCrashDelay_exponentialCap(t *testing.T) {
	// 退避指数增长并封顶 64s
	// crashDelay = max(6, 1<<min(consec-1,6))
	cases := []struct{ consec, want int }{
		{3, 6},
		{4, 8},
		{5, 16},
		{6, 32},
		{7, 64}, // cap
		{9, 64}, // cap
	}
	for _, c := range cases {
		if d := crashDelay(c.consec); d != c.want {
			t.Fatalf("crashDelay(%d)=%d, want %d", c.consec, d, c.want)
		}
	}
}

func TestCrashWindow_underLimit(t *testing.T) {
	// 窗口内崩溃数 < limit -> 不熔断
	now := time.Now()
	crashes := []time.Time{
		now.Add(-5 * time.Second),
		now.Add(-4 * time.Second),
	}
	keep, tripped := crashWindow(crashes, now, 60*time.Second, 3)
	if tripped {
		t.Fatal("want no trip with only 2 crashes")
	}
	if len(keep) != len(crashes) {
		t.Fatalf("keep len=%d, want %d (all within window)", len(keep), len(crashes))
	}
}

func TestCrashWindow_tripped(t *testing.T) {
	// 窗口内崩溃数 >= limit -> 熔断
	now := time.Now()
	crashes := []time.Time{
		now.Add(-1 * time.Second),
		now.Add(-2 * time.Second),
		now.Add(-3 * time.Second),
	}
	keep, tripped := crashWindow(crashes, now, 60*time.Second, 3)
	if !tripped {
		t.Fatal("want trip with 3 crashes in window")
	}
	if len(keep) != 3 {
		t.Fatalf("keep len=%d, want 3", len(keep))
	}
}

func TestCrashWindow_oldOutsideWindow(t *testing.T) {
	// 窗口外(>60s)的旧崩溃被剔除，不参与熔断判定
	now := time.Now()
	crashes := []time.Time{
		now.Add(-120 * time.Second), // 已过期
		now.Add(-90 * time.Second),  // 已过期
		now.Add(-1 * time.Second),   // 窗内
		now.Add(-2 * time.Second),   // 窗内
	}
	keep, tripped := crashWindow(crashes, now, 60*time.Second, 3)
	if tripped {
		t.Fatal("want no trip: only 2 crashes inside window")
	}
	if len(keep) != 2 {
		t.Fatalf("keep len=%d, want 2", len(keep))
	}
}

func TestMutexNameDistinctFromNode(t *testing.T) {
	// M9: 启动器互斥量与节点互斥量命名隔离，前缀不同永不互撞（P2-14）
	launcherName := "AgentNodeLauncher_" + shortSHA1("C:\\data")
	nodeName := "AgentNode_" + shortSHA1("C:\\data")
	if launcherName == nodeName {
		t.Fatalf("launcher and node mutex names collide: %s", launcherName)
	}
	const prefix = "AgentNodeLauncher_"
	if len(launcherName) != len(prefix)+24 {
		t.Fatalf("launcher mutex name malformed: %s", launcherName)
	}
}
