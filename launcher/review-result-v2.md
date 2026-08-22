# launcher Go v1 看门狗启动器 — 代码复评（review-g 修复验证）

> 复评日期：2026-08-22
> 复评对象：`e:\agent-node\launcher\`（watchdog.go / main.go / paths.go / health.go / panel.go / util.go / win32.go / icon.go / logger.go / build.ps1 / watchdog_test.go，共 11 个文件）
> 对照基准：review-result.md（review-g 评审结论，P1-1~P1-5 / P2-6~P2-15 / P3 1-8 + 过度设计清单）
> 验证环境：Windows 10 / go1.27.0 windows/amd64（C:\Go\bin\go.exe）

---

## 1. 总评

**通过（可投产）。**

review-g 列出的 5 项 P1 阻断项（P1-1~P1-5）已全部真正修复并有 file:line 证据可查；P2 清单中 9/10 项已修复，仅 P2-14 为部分覆盖（核心纯函数已测，onChildExit 三分支未直接单测，属可接受范围）；工具链验证 `go vet ./...`、`go test ./...`（6/6 PASS）、`go build -ldflags "-H windowsgui -s -w"` 全部通过，产物 PE Subsystem=2（Windows GUI，双击无黑窗）。本轮新发现 2 个 P2（不阻断投产，建议合入前修）与若干 P3，详见 §4。

---

## 2. 工具链验证结果

| 项目 | 命令 | 结果 |
|---|---|---|
| 版本 | `go version` | go1.27.0 windows/amd64 ✅（满足 go.mod `go 1.27`） |
| 静态检查 | `go vet ./...` | 通过，exit 0 ✅ |
| 单测 | `go test ./... -v` | 6/6 PASS（crashDelay×2、crashWindow×3、互斥量命名×1），exit 0 ✅ |
| 构建 | `go build -trimpath -ldflags "-H windowsgui -s -w" -o agent-node-launcher.exe .` | 成功，exit 0，产物 7,709,696 bytes ✅ |
| 子系统 | PE 解析（opt+0x44） | **Subsystem=2（Windows GUI）** ✅ 无黑窗 |

---

## 3. 逐项勾对表（review-g 清单）

### P1（必须修复，5/5 ✅）

| # | 判定 | file:line 证据 | 说明 |
|---|---|---|---|
| P1-1 健康判定接入 HTTP + 就绪判定 | ✅ | watchdog.go:89-95（nodeHealthy 去惰性回退：`nodeAliveFast() && probeOnce()!=nil`）；main.go:92-96（托盘 `s.alive && s.healthy`→绿，否则红）；watchdog.go:132-134（spawn 后 40s 窗口 booting 就绪判定）；watchdog.go:118-155（evaluateHealth 快照 + reason 分文案） | HTTP 失败不再回退进程判定，数学上不再恒等 nodeAliveFast；"进程活但面板不通 → 红 + tooltip 面板异常"（watchdog.go:152）已落地；spawn 不再即绿 |
| P1-2 M3a 宽限期为底 | ✅ | watchdog.go:364-368（`crashDelay = max(6, 1<<min(consec-1,6))`）；watchdog.go:346-348（`nextSpawn = now + crashDelay`，注释同步修正） | 6s 宽限为底、退避取大，restart_self 双 spawn 争锁竞态消除 |
| P1-3 退出码三分支 | ✅ | watchdog.go:291-327（case 0→clean 不重启 +24h 等 Start；case 1→锁活转监督/锁死记崩溃；default→一律 recordCrash） | exit 0 不再重启+计熔断；非 1 码不再误转监督。附注：default 分支未按原建议"锁活 PID 顺带转监督"，但 reconcile 3s 内兜底转监督并清零（watchdog.go:409-417），语义等价 |
| P1-4 熔断阈值 | ✅ | watchdog.go:336-344（`crashWindow(60s, 3)`→tripped→`nextSpawn=Time{}`+CIRCUIT BREAK 日志）；watchdog.go:353-362（crashWindow 纯函数）；watchdog.go:423（`paused \|\| tripped` 不再 spawn）；解熔断：watchdog.go:399-407/409-417（健康恢复/活 PID）、439-445（cmdStart） | 1 分钟 3 次崩溃停自动重启 + 红态 tooltip"已熔断"（watchdog.go:140）已落地 |
| P1-5 D6 安装引导 | ✅ | main.go:34-61（未安装→confirmInstall 是/否→runInstallScript→复检→继续；已装不完整→驻留托盘红态不退出）；panel.go:28-40（MB_YESNO + 隐藏 powershell `irm <installURL> \| iex`）；panel.go:10（URL 与 ps1:98 同源） | 有安装交互、不完整安装驻留红态。**新发现 P2-新1 见 §4** |

### P2（建议修复，9/10 ✅ + 1 部分 ⚠️）

| # | 判定 | file:line 证据 | 说明 |
|---|---|---|---|
| P2-6 PYTHONPATH 合并前置 | ✅ | paths.go:120-140（遍历 env 找既有 PYTHONPATH，合并为 `sitePkg + ";" + cur`，无则 append） | 不再丢已有值，与 ps1:105 语义一致 |
| P2-7 M6 活 URL | ✅ | health.go:62-72（livePanelURL：probeOverview 成功用 ov.PanelUrl，失败回退 panel.url）；main.go:22-26、105-108 均优先 livePanelURL | 每次点击实时探测（2s 超时），行为正确 |
| P2-8 tooltip 详情 | ✅ | watchdog.go:16-28（stateSnapshot 含 version/pid/panelURL/reason）；watchdog.go:126-131（从 overview 填充）；main.go:122-138（formatTooltip 格式化）；红态分文案：watchdog.go:137-153 | version/PID/面板URL/红态原因（组件缺失/已熔断/已停止/未运行/启动中/面板异常）齐全 |
| P2-9 pyvenv.cfg 解析日志 | ✅ | paths.go:67（home 命中）、74（PATH 回退）、79（兜底）均有 logL 记录解析结果 | M7 硬性要求满足 |
| P2-10 互斥量实现细节 | ✅ | win32.go:44-52（先判 `h==0` 再查 `cerr==ERROR_ALREADY_EXISTS`，用 CreateMutex 返回 err 而非 GetLastError）；util.go:8-17（shortSHA1 更名，注释如实声明 sha1） | 检查顺序与命名误导均已修 |
| P2-11 BREAKAWAY 重试 | ✅ | watchdog.go:178-191（ACCESS_DENIED 时去 flag 重试一次 + log warning）；watchdog.go:178（注释说明尽力而为语义） | 宿主 Job 禁止 breakaway 时不再起不来 |
| P2-12 数据竞争 | ✅ | watchdog.go:56（`snap atomic.Value`）；watchdog.go:118-155（evaluateHealth 仅在 run 循环写）；main.go:91（托盘 goroutine 只读 loadSnapshot）；main.go:109-114（菜单动作经 cmds channel 投递） | 托盘线程与 run 循环无共享可变状态直读，数据竞争消除 |
| P2-13 构建脚本 + windowsgui | ✅ | build.ps1:16（`go build -trimpath -ldflags "-H windowsgui -s -w"`）；build.ps1:14-15（GOOS/GOARCH=windows/amd64） | 实测构建产物 PE Subsystem=2（GUI）✅ |
| P2-14 补测试 | ⚠️ 部分 | watchdog_test.go:8-98（crashDelay 宽限底/指数封顶、crashWindow 三分支、互斥量命名隔离共 6 测，全部 PASS） | 原建议"至少补：onChildExit 三分支、退避、熔断、reconcile 门控 + 互斥量命名"——退避/熔断/命名已测，**onChildExit 三分支与 reconcile 门控未直接单测**（依赖 readLockPID/pidAlive 系统调用，未抽取纯函数接口），可接受但留白 |
| P2-15 pyvenv.cfg BOM | ✅ | paths.go:40-41（`line = strings.TrimPrefix(line, "\uFEFF")`） | 双保险：unicode.IsSpace 已含 \uFEFF，TrimSpace 先行清理后再 strip 一次 |

### P3（可选优化，4/8 ✅ / 2 ⚠️ / 2 ❌）

| # | 判定 | 证据/说明 |
|---|---|---|
| P3-1 轮转注释对齐设计 | ⚠️ | logger.go:19 有"// 简单轮转"注释，但未明确说明与设计 10MB×5 的简化关系，建议补一句 |
| P3-2 stop 超时日志措辞 | ✅ | watchdog.go:280 已改为 "stop timeout: node alive; handing to reconcile"，不再误导 |
| P3-3 paths.go 死变量 | ✅ | resolveLauncher 重构为三元组返回（paths.go:55-81），`_ = data` 已消除 |
| P3-4 syncExisting 不可达分支注释 | ⚠️ | watchdog.go:223-242 incomplete 分支仍保留且无注释标注"防御性/不可达"（main.go:53-60 已前置拦截，此分支正常流程不可达） |
| P3-5 stopNode 8s 轮询简化 | ❌ | watchdog.go:265-271 保留 8s 轮询未简化——未采纳，但保留更稳妥（等待锁清理时机），可接受 |
| P3-6 go.mod 版本 | ✅ | go 1.27 保留，本机 go1.27.0 实测构建通过，无需降级；注意同事/CI 机需 ≥1.27 |
| P3-7 启动时残留锁清理 | ✅ | watchdog.go:226-229（syncExisting 开头：锁 PID 已死则 os.Remove）已实现 |
| P3-8 探测频度 | ✅ | probeOnce 5s 节流（watchdog.go:99），reconcile 3s 内复用缓存；图标 2s 读快照——比原 3s 一次 HTTP 更省，虽未降到建议的 15-30s 但方向正确 |

---

## 4. 本轮新发现问题

### P2-新1（建议合入前修）：main.go:56-59"已装不完整"驻留红态分支未启动 run 循环

- **位置**：main.go:53-60
- **问题**：该分支创建 `w` 并 `systray.Run`，但**没有 `go w.run()`**。后果：①`evaluateHealth()` 从不执行，快照停留在初始 `reason:"启动器就绪，监测中…"`，tooltip **不显示"组件缺失"原因**（图标为红但用户不知缘由）；②菜单 Start/Stop/Restart 命令经 `w.cmds` 投递后无人消费（channel 缓冲 8，静默堆积）。
- **修复**：改为 `w.incomplete = missing; go w.run(); systray.Run(...)`——run 循环中 syncExisting（watchdog.go:223-225）与 reconcile（watchdog.go:395-397）的 incomplete 分支已能正确处理"不 spawn、驻留红态"，且托盘能实时读到"组件缺失"reason。

### P2-新2（建议合入前修）：健康恢复/转监督路径未清 crashes 数组，窗口内旧崩溃可能引发误熔断

- **位置**：watchdog.go:399-407（健康清零）、409-417（转监督清零）
- **问题**：这两处只清 `consecFail`（和 tripped 时的 crashes），**未 tripped 时保留 `w.crashes` 旧时间戳**。场景：60s 窗口内崩 2 次 → 健康恢复（consecFail=0，crashes 仍含 2 条）→ 再崩 1 次 → `recordCrash` 的 `crashWindow(60s,3)` 把窗口内旧 2 条计入 → 误判熔断。语义上"健康恢复后"应重新计窗口。
- **修复**：两处清零分支统一加 `w.crashes = w.crashes[:0]`（cmdStart/cmdRestart 已如此处理）。

### P3 级新发现

1. **watchdog.go:126-131 vs 125**：`evaluateHealth` 中 `s.healthy` 取自 probeOnce 缓存，未与 `s.alive` 联动——进程死但缓存未过期时快照出现 `alive=false, healthy=true` 自相矛盾（托盘判定 `s.alive && s.healthy` 已正确，无实际影响）；建议 `s.healthy = s.alive && s.healthy`。
2. **watchdog.go:244-247**：spawnHeld 失败（如 exe 缺失）时 holding=false 且未设置 nextSpawn，reconcile 每 3s 重试并打日志（无退避）。正常流程 main 已拦截不完整安装，仅异常场景触发，建议失败也计一次退避。
3. **paths.go:17-18**：`lockPath`/`panelURLPath` 连续声明 gofmt 对齐不规范（`go vet` 不报），跑 `gofmt -w` 可修。
4. **build.ps1:10-11**：只跑 `go vet` 未跑 `go test`，建议构建前加 `go test ./...`。
5. **watchdog.go:99**：probeOnce 5s 节流在失败场景下 5s 后才重试，tooltip 红态原因最多滞后 5s——可接受，无需改。

---

## 5. 最终结论

**结论等级：通过（可投产）。**

- review-g 的 5 项 P1 阻断项全部修复（✅ 证据见 §3）；
- 10 项 P2 中 9 项修复、1 项（P2-14）部分覆盖（不影响投产）；
- P3 主要项已落地（残留锁清理、stop 日志、死变量、BOM、探测节流）；
- 工具链三连验证全部通过，产物为 Windows GUI 子系统（无黑窗）；
- 本轮新增 2 个 P2（P2-新1：不完整分支未跑 run 循环；P2-新2：健康恢复未清崩溃窗口）**不构成阻断**——前者仅在"安装不完整"异常路径触发（提示缺失但功能正确），后者为低概率窗口竞态且有用户 Start/手动干预兜底。**建议合入前顺手修掉**，修完可视为完全收敛。

**剩余阻断项：无。**
**合入前建议（不阻断）**：P2-新1、P2-新2；P3 各项可随版本迭代处理。
