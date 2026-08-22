# launcher Go v1 看门狗启动器 — 代码评审（review-g）

> 评审日期：2026-08-22
> 评审对象：`e:\agent-node\launcher\`（go.mod / main.go / watchdog.go / paths.go / health.go / win32.go / logger.go / icon.go / panel.go / util.go，共 ~750 行）
> 对照基准：`docs/launcher-watchdog-design.md`（设计定稿）、`scripts/agent-node.ps1`（Resolve-NodeLauncher / Get-VenvSitePackages / Start-Node）、`node/core.py`（单实例 / 退出码 / stop / overview）、`node/main.py`（退出码路径）、`server/panel.py`（/api/overview、/api/health、panel.url）
> 评审方法：逐文件通读 + 契约字段实测核对（overview 字段、node.lock 格式、互斥量命名、restart_self 退出码）+ 设计验收点逐项勾对

---

## 1. 结论等级

**需修改（不能直接投产）**

骨架方向正确：持有/监督双模式、childDone 回传、互斥量命名隔离（M9）、无 Job Object 连坐（D7/M8）、node.lock PID 判定（C4）、stderr 重定向（M7）均已按设计落地，无编译级问题（逐文件检查 import/符号/API 签名均自洽）。

但存在 **5 项 P1 级设计违背/实现偏差**，其中 2 项直接命中设计"看门狗灵魂"条款：

1. **健康判定失效**（§4.2/§7.1）：HTTP `/api/overview` 探测在实现里**行为上完全惰性**——`nodeHealthy()` 的 HTTP 失败回退使其恒等于 `nodeAliveFast()`，且托盘图标逻辑（main.go:67-76）从不使用 HTTP。结果是"面板挂而核心活"仍显示 🟢，与设计"判 🔴 + tooltip 面板异常"相反。
2. **M3a 宽限期被覆盖**（watchdog.go:190→195）：6s 宽限赋值被退避计算立即覆盖，restart_self（退出码 1 + 500ms 后拉起新进程）场景下看门狗会在 t≈2s 抢先 respawn，与 restart_self 新进程争锁（设计 §6 明确要避免的竞态）。
3. **M2 退出码分发缺失**（watchdog.go:170-197）：未区分 exit 0（设计=不重启）与非 1 码（设计=崩溃重启），exit 0 且无活锁时仍重启+计熔断；任意非 1 码且锁 PID 活时都误转监督（设计仅 exit==1 才查锁）。
4. **熔断阈值缺失**（D2/§6）：只有指数退避（1s..64s），无"1 分钟内崩溃 ≥3 次 → 停止重启 + 红 + tooltip 指 node.log"。
5. **D6 未实现**：未安装时仅静态 MessageBox + 退出，无设计要求的"是/否 → 隐藏调用 install.ps1(irm) 安装"；"已装但不完整"分支设计上应驻留托盘 🔴，实现直接退出（无托盘）。

---

## 2. 设计验收点逐项勾对

| 验收点 | 实现 | 判定 | 说明 |
|---|---|---|---|
| D1 便携入口+固定家 `%LOCALAPPDATA%\agent-node` | paths.go:11-17 | ✅ | root() 固定 LOCALAPPDATA，exe 位置无关 |
| D2 自动重启+退避熔断 | watchdog.go:192-196 | ⚠️ | 退避有，**熔断阈值无**（见 P1-4） |
| D6 首次未装 → install.ps1 | main.go:31-35 + panel.go:15-21 | ❌ | 仅 MessageBox，无 是/否、无 irm 调用、直接退出（见 P1-5） |
| D7 退出不杀节点 | watchdog.go:82-86、main.go:100-102 | ✅ | 无 Job Object；BREAKAWAY_FROM_JOB 兜底（见 P2-11 风险） |
| 进程模型：持有 vs 监督 | watchdog.go:68-134、201-251 | ✅ | spawnHeld 父→子 + childDone；syncExisting/reconcile 转监督；Windows 无法事后接管的限制处理正确 |
| 健康判定 = 锁 PID + HTTP | health.go:42-63、main.go:67-76 | ❌ | HTTP 惰性 + 图标不用 HTTP（见 P1-1） |
| C1 四元组 exe | paths.go:52-77 | ✅ | 三级回退与 ps1:53-66 一致 |
| C1 args / cwd | paths.go:120 | ✅ | `-m node.main --data-dir <DATA>`、cwd=app 均正确 |
| C1 env PYTHONPATH 前置保留 | paths.go:116-119 | ❌ | 重复键追加，覆盖已有值（见 P2-6） |
| C1 pyvenv.cfg 解析 BOM | paths.go:29-50 | ⚠️ | 注释声称处理 BOM，实际未 strip（见 P2-15） |
| C1 就绪判定（spawn 后 ≤40s 轮询） | — | ❌ | 完全未实现；spawn 即绿（见 P1-1 附注） |
| M2 退出码 1 语义 | watchdog.go:170-197 | ⚠️ | exit==1 分支逻辑对，但未按码分发（见 P1-3） |
| M3a 宽限期 5-8s | watchdog.go:188-190 | ❌ | 被 195 行覆盖（见 P1-2） |
| M5 残留锁清理 | watchdog.go:158-161、183-186 | ✅ | stopNode/onChildExit 均清死锁；启动时未清既有残留（P3，见下） |
| M7 日志 | logger.go:15-27 | ⚠️ | launcher.log + stderr 重定向✅；轮转 10MB×1 非 ×5；**pyvenv.cfg 解析结果未记日志**（§5.1 硬性要求） |
| M8 显式停止 taskkill /T /F | watchdog.go:136-166、health.go:95-98 | ✅ | 与 ps1:127 一致 |
| M9 互斥量命名隔离 | win32.go:39-56、util.go:9-16 | ⚠️ | 前缀 `AgentNodeLauncher_` + sha1[:24] 与节点 `AgentNode_` 区分✅；实现细节见 P2-10 |
| 单实例"已有看门狗→退出" | main.go:18-25 | ✅ | 多送"打开面板"属良性增强 |
| 左键开面板用探测活 URL（M6） | main.go:83-85 | ❌ | 直接读 panel.url（见 P2-7） |
| tooltip 细节（§7.3） | main.go:68-76 | ❌ | 无 version/PID/面板 URL，无红态原因（见 P2-8） |

---

## 3. P1 错误/遗漏清单（必须修复）

### P1-1 健康判定失效：HTTP /api/overview 完全惰性（§4.2/§7.1 核心违背）
- **位置**：health.go:52-63（nodeHealthy 回退）、watchdog.go:222（唯一使用处）、main.go:67-76（托盘图标）
- **问题**：`nodeHealthy()` 在 HTTP 失败时回退 `return w.nodeAliveFast()`——由于前置已判进程活，**nodeHealthy() 数学上恒等于 nodeAliveFast()**，HTTP 探测对任何行为路径零影响；托盘图标更是只查 `w.holding || nodeAliveFast()`。设计要求"进程活 + HTTP 200 = 🟢；进程活 + HTTP 不通 = 🔴 面板异常"被实现为"进程活 = 恒绿"。
- **修复**：①托盘图标改为三态判定：`nodeAliveFast && probeOverview OK → 绿`；进程活但 HTTP 不通 → 红 + tooltip"面板异常"（v1 只有红/绿两态，红态即可，tooltip 给原因）；②`nodeHealthy()` 去掉 HTTP 失败回退，仅作熔断清零闸门时也应按"HTTP 必须通"判定；③顺带实现 §5.6 就绪判定：spawn 后轮询 ≤40s（lock + panel.url + /api/health 或 /api/overview 200）→ 才转绿，超时红 + tooltip 指 node.log——当前 spawn 即绿，崩溃循环期间图标绿/红闪烁，语义错误。

### P1-2 M3a 宽限期被退避覆盖 → restart_self 双 spawn 竞态（§6）
- **位置**：watchdog.go:190 与 195
- **问题**：`w.nextSpawn = time.Now().Add(6s)`（宽限）在下一行被 `time.Now().Add(exp * time.Second)`（退避，首次仅 1s）**无条件覆盖**，6s 宽限从未生效。restart_self 时序：旧进程 Stop-Process -Force（退出码 1）→ 500ms 后新进程启动（写锁需 ~1.5-2.5s）→ 看门狗 onChildExit 时锁 PID 已死 → 清锁 → 退避 1s → reconcile 在 t≈2-3s 时宽限未过即 respawn，与 restart_self 新进程争内核互斥量（靠节点原子互斥兜底，但正是设计 M3a 要避免的"看门狗 vs restart_self 互踩"）。
- **修复**：`w.nextSpawn = time.Now().Add(time.Duration(max(6, exp)) * time.Second)`（宽限为底，退避取大）；并修正 188-190 行注释与行为一致（reconcile 的 3s 轮询本身已能发现新活 PID 转监督，无需额外重查逻辑）。

### P1-3 M2 退出码分发缺失（§6 判别规则）
- **位置**：watchdog.go:170-197（onChildExit）
- **问题**：设计判别规则是 `exit==0 → 不重启`、`exit==1 → 查锁（活→监督/死→崩溃退避）`、`其他非 0 → 崩溃退避重启`。实现未按码分发：①exit 0 且锁无活 PID → 仍 `consecFail++` + 退避重启（违背"正常退出不触发重启"）；②exit=2/3 等真实崩溃码且锁恰有活 PID（如用户手动另起了节点）→ 误转监督、不计崩溃（掩盖真实崩溃信号）。
- **修复**：按设计实现三分支——`code == 0`：直接 log + 置 paused=false 但 nextSpawn 无限期（或维持"不重启"语义，靠用户 Start 拉起）；`code == 1`：现有 M2 查锁逻辑；`else`：一律按崩溃计（锁活 PID 场景仍可顺带转监督，但须打 warning 日志而非静默）。

### P1-4 熔断阈值缺失（D2/§6）
- **位置**：watchdog.go:192-196（仅有退避，无熔断）
- **问题**：设计要求"1 分钟内崩溃 ≥3 次 → 停止重启，托盘 🔴 + tooltip 指 data/node.log"。当前只有指数退避到 64s，崩溃循环永远不会停止，托盘也无法表达"已熔断"。
- **修复**：新增熔断态（可用 `consecFail>=3 && 窗口≤60s` 判定或独立字段）：熔断后 reconcile 不再 spawn（等价 paused 语义）、tooltip 显示"已熔断停止，请查看 node.log"；用户点 Start/Restart 或健康恢复（HTTP 200）时解熔断清零。

### P1-5 D6 未实现：无安装引导，不完整安装直接退出
- **位置**：main.go:31-35、panel.go:15-21
- **问题**：设计 §3 状态机：未安装 → 弹"需要安装，是否继续？"（是 → 隐藏 PowerShell `irm <install.ps1> | iex`；否 → 退出）；已装但不完整 → 驻留托盘 🔴 + tooltip"组件缺失，请重新安装"。实现：两种情况都是静态 MessageBox（0x10 错误图标）"请在 PowerShell 中执行安装后重试"然后 `return` 退出——既没有安装交互，也不驻留托盘。
- **修复**：①notifyInstall 改为 MessageBox 是/否（MB_YESNO），是 → `execCommand("powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", "irm https://raw.githubusercontent.com/weifeng-work/agent-node/main/scripts/install.ps1 | iex")`（与 ps1:98 同源 URL）→ 安装后继续启动流程；否 → 退出；②"已装但不完整"分支改为进入托盘红态而非退出（installedRoot 细化缺失项，tooltip 显示"组件缺失"）。

---

## 4. P2 清单（建议修复）

| # | 位置 | 问题 | 修复建议 |
|---|---|---|---|
| P2-6 | paths.go:116-119 | C1 env 违约：`append(env, "PYTHONPATH=...")` 产生重复键，Go os/exec 去重后**后值覆盖前值**，已有 PYTHONPATH 丢失（ps1:105 是 `"$sp;$saved"` 前置保留） | 遍历 os.Environ() 找出已有 PYTHONPATH，合并为 `PYTHONPATH=sitePkg + ";" + 已有值` |
| P2-7 | main.go:83-85、20-23 | M6 违约：打开面板直接读 panel.url，未用探测活 URL | 复用 probeOverview 的 `PanelUrl` 字段（nodeHealthy 为真时优先用它），panel.url 仅作回退 |
| P2-8 | main.go:68-76 | tooltip 未按 §7.3 给 version/PID/面板 URL 与红态原因（面板异常/已熔断/组件缺失） | probeOverview 结果挂到 watchState（探测到的 overview 字段），tooltip 格式化显示；红态按原因分文案 |
| P2-9 | paths.go:52-77 | M7 违约：§5.1 要求"pyvenv.cfg 解析结果必须写日志"，resolveLauncher 未记任何日志 | 在 resolveLauncher 内 logL 输出：home 命中/PATH 回退/兜底 + 解析出的 site-packages |
| P2-10 | win32.go:39-56、util.go:9-16 | M9 实现细节：①`GetLastError()` 检查顺序应先判 `h==0` 再查已存在，且更稳的是直接用 `CreateMutex` 返回的 err（x/sys/windows 已封装）；②shortSHA256 函数名写 SHA256 实际用 **sha1**（crypto/sha1），命名误导（输出值恰与设计 sha1 一致，功能正确） | ①改为 `h, err := CreateMutex(...); if h==0 {return 0,false}; if err==windows.ERROR_ALREADY_EXISTS {关闭; return 0,false}`；②改名 shortSHA1 或改真用 sha256（改真 sha256 也无妨，前缀已隔离） |
| P2-11 | watchdog.go:85 | BREAKAWAY_FROM_JOB 风险：若启动器自身在**不允许 breakaway 的 Job** 中（IDE/服务/沙箱拉起），CreateProcess 直接 ERROR_ACCESS_DENIED，节点起不来 | spawn 失败（ERROR_ACCESS_DENIED）时去掉该 flag 重试一次并 log warning；注释补一句该 flag 仅在宿主 Job 允许时生效 |
| P2-12 | main.go:62-78 vs watchdog.go:92 | 数据竞争：托盘 ticker goroutine 每 2s 直接读 `w.holding`（写侧在 run 循环），`go build -race` 必报；amd64 上实践无撕裂但属 UB，状态可能滞后一拍 | 改 `atomic.Bool`（Go 1.19+），写侧 Store/读侧 Load；或把图标状态计算收进 run 循环经 channel 下发 |
| P2-13 | launcher/ 目录 | 无构建脚本、未标注 `-ldflags "-H windowsgui"`：默认 console 子系统，**双击会闪黑窗**，违背"双击即用零命令行"定位 | 新增 build.ps1/bat：`go build -ldflags "-H windowsgui -s -w" -o agent-node-launcher.exe .`（GOOS=windows GOARCH=amd64） |
| P2-14 | launcher/ 目录 | 设计 v1 落地步骤第 8 条要求集成测试（崩溃重启/熔断/多实例竞态/双层互斥量不互撞），无任何测试文件 | 至少补 watchdog 状态机单测（onChildExit 三分支、退避、熔断、reconcile 门控）+ 互斥量命名互撞测试 |
| P2-15 | paths.go:30-49 | readPyvenvCfg 注释声称"BOM…处理"，代码未 strip `\uFEFF`；若 pyvenv.cfg 带 BOM（部分工具写入），首个 key 变 `\ufeffhome` → home 解析失败 | 首行 `strings.TrimPrefix(line, "\uFEFF")` |

---

## 5. P3 清单（可选优化）

1. **logger.go:18-20**：轮转仅保留 1 份 `.old`，设计为 RotatingFileHandler 10MB×5——可接受简化，建议注释说明。
2. **watchdog.go:165**：stopNode 8s 超时后仍 log "node stopped"（误导）；超时应 log "stop timeout, node still alive" 且不置 holding=false 语义不变（交给 reconcile 转监督）。
3. **paths.go:75**：`_ = data` 死变量（resolveLauncher 内 data 计算后未用），删除或并入返回值。
4. **watchdog.go:125-129**：syncExisting 的"installed root incomplete; supervisor-only"分支不可达（main() 未安装即 return）——防御性代码，可留可删，注释标注。
5. **watchdog.go:151-157**：stopNode 8s 轮询等待可简化为 taskkill 后 1-2s 单次检查；超时残留锁交给 reconcile 的 M5 分支清理。
6. **go.mod:3**：`go 1.27` 需本机工具链 ≥1.27 才可构建（2026-08 已发布，属边界版本），构建前确认；若 CI/同事机没有，可降 1.23+（代码仅用内置 min/max 与 atomic，1.21+ 即可）。
7. **启动时残留锁清理**：syncExisting 中若锁 PID 已死，顺手 `os.Remove(lockPath)`，避免首次 spawn 触发 core.py:334-339 的"异常退出"模态告警（当前只能靠节点自愈，会弹一次警告）。
8. **reconcile HTTP 探测频度**：若按 P1-1 接入图标，/api/overview 每 3s 拉一次（payload 含 executors/notifications，较重）可降到 15-30s；图标刷新仍 2s 但复用最近一次探测结果。

---

## 6. 过度设计清单

1. **HTTP 探测层整体（最显著）**：`nodeHealthy()` 的回退使其恒等于 `nodeAliveFast()`，托盘又不用 HTTP——`probeOverview` + `overview` 结构体 + 每 3s HTTP 请求目前是**纯死代码**（唯一副作用是每 3s 打一次面板）。修复方向二选一：按设计接入图标/tooltip（推荐，恢复设计语义）；或砍掉 HTTP 层退回纯进程判定（不推荐，违背定稿）。
2. **overview 结构体 6 字段全部未消费**：解析后无一使用（与上同源）。若接入 tooltip 则 version/pid/panelUrl 有用武之地；否则解析可退化到状态码 200 检查。
3. **stopNode 的 8s 轮询 + taskkill /T /F + 锁清理三路叠加**：显式停止路径 taskkill /T /F 已覆盖进程树，8s 轮询只为等锁清理时机——可简化（见 P3-5），不算错误但成本偏高。
4. **BREAKAWAY_FROM_JOB + CREATE_NO_WINDOW 双 flag**：CREATE_NO_WINDOW 必要；BREAKAWAY 属"尽力而为"（宿主 Job 不允许时反而致败，见 P2-11），其防连坐语义其实主要靠"不用 Job Object"本身保证——可考虑去掉 BREAKAWAY 只保留 CREATE_NO_WINDOW + 文档说明，或保留但加重试。
5. **syncExisting 不可达分支**（P3-4）属防御性过度，一行注释即可。

---

## 7. 修复落地顺序（按优先级）

| 阶段 | 内容 | 对应 |
|---|---|---|
| **先修（阻断投产）** | P1-1 健康判定接入 HTTP + 就绪判定；P1-2 M3a 宽限取 max(6,exp)；P1-3 退出码三分支；P1-4 熔断阈值；P1-5 D6 安装交互 | 设计 §4.2/§5.6/§6/D2/D6 |
| **次修（合入前建议）** | P2-6 PYTHONPATH 合并；P2-7 M6 活 URL；P2-8 tooltip 详情；P2-9 pyvenv.cfg 解析日志；P2-12 数据竞争；P2-13 构建脚本 + windowsgui；P2-14 补测试 | C1/M6/M7/M9/质量 |
| **随修** | P2-10/11/15、P3 各项 | 健壮性/一致性 |

---

## 8. 已确认正确的实现（无需改动）

- 持有/监督双模式与 Windows 进程模型限制的处理（spawn 时定父子的不可逆性认知正确，监督模式仅读锁判活）。
- childDone 通过 Wait goroutine 回传真实退出码，spawn 句柄即"双保险"（PID 复用免疫）——设计 §11 要求的双保险已具备。
- M9 命名隔离：`AgentNodeLauncher_`+sha1(dataDir)[:24] 与节点 `AgentNode_`+sha1(data_dir)[:24] 前缀不同，永不互撞（dataDir 的 / vs \ 表示差异不影响，因前缀已隔离）。
- M5 两处清锁（stopNode / onChildExit）位置正确，能避免 core.py 残留锁模态告警。
- M8：无 Job Object + 显式 taskkill /T /F，托盘退出（onTrayExit 仅 close quit）绝不杀节点。
- C1 exe 三级回退与 ps1 语义逐行对齐；cwd=app、args 四元组正确。
- overview 结构体字段与 core.overview() 实测输出一致（nodeId/version/pid/status/panelUrl/uptimeSec 均在）。
