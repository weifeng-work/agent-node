# Changelog

本项目按 [重构设计方案.md](docs/重构设计方案.md) 施工，每阶段记录于此。

## [0.5.0] - 2026-08-20 阶段五：CodeBuddy 双执行器 + 可见弹窗 + 面板四页签 + SKILL/深文档

### CodeBuddy 执行器（用户拍板项）
- **codebuddy-cli**（非交互 fresh，并发 3）: psmux 独立会话 + 弹窗；prompt 经 stdin；
  PS `Tee-Object` **实时显示对话流 + 捕获**；完成后醒目重放回复 + 15s 停留；
  三重 UTF-8 编码防 GBK 乱码；登录态检测（auth 标记 → 明确指引）
- **codebuddy-tui**（交互式常驻，并发 1 串行）: psmux 常驻 TUI 会话跨任务复用 +
  attach 弹窗（人类观察 TUI 对话界面与工具调用全过程）；trust 对话框自动通过；
  就绪检测用 TUI 特征字符 █/│（欢迎屏动态内容防"静止"误判）
- **psmux v3.3.8 随包分发**（bin/，PATH 前置）；legacy/ 旧代码已删除

### 弹窗可见性三连环修复（对照实验法定位）
1. **窗口站诊断**: cmd/notepad 对照实验证明进程树可弹窗 → 问题在实现
2. **attach 闪退根因**: Python list2cmdline 与 cmd /S 嵌套引号转义冲突 →
   **批处理文件中转**（cmd /c batch.cmd + CREATE_NEW_CONSOLE）
3. **注入截断**: psmux send-keys 超长行（~600 字符）路径被截 → **长命令写
   run.ps1 脚本文件**，pane 只敲短命令（+ ExecutionPolicy Bypass）
4. **TUI Enter 被吞**: 文本注入后立即 Enter 无效 → **延时 1.5s + 提交验证**
   （文本进入对话区且输入框清空）+ 3 次重试
5. 实时刷新验证: attach 窗口逐秒输出实测正常（数字递增确认）

### 面板改造（用户需求）
- **文件四子页签**: 文件浏览器（浏览所有连接节点）/ 推送文件（节点选择 +
  文件/文件夹选择器 + 目标目录可选 + 发送历史）/ 收件目录（内容 + 收件
  历史含来源节点）/ 同步目录（文件列表 + 确认同步按钮 + **各节点同步
  完成度百分比进度条**——未确认节点人类可见 + 加入同步目录选择器）
- 新端点: `/api/files/inbox`（内容+历史）、`/api/sync/status`（per 设备
  completion）、`/api/sync/add`（文件加入同步目录）
- **聊天身份标识**: 气泡显示发送者名称（本机名"（本机）"/对方节点名，类微信）
- 聊天刷新 3s（原 bug 12s）且静默刷新（消息数变化才重绘，不打断输入焦点）

### 文档（用户拍板项）
- **skill/SKILL.md**: 双接入方式（MCP/CLI）+ caller_id↔收件箱认知 + 三概念
  防混淆 + 工作流 + 错误码表 + **AI 行为边界（禁改源码/禁动节点进程）** +
  mihomo 排查指引
- **docs/协议与架构.md**（深文档，门控 2.17.7）: 架构总览 + 线级协议完整规范 +
  执行器实战坑汇总 + 面板/MCP/CLI 三层同源说明 + 已知边界

### 双机同步
- 全部更新推送 IKUN（含 psmux 二进制）；IKUN 也有 codebuddy CLI →
  **双端各 3 执行器**（workbuddy/codebuddy-cli/codebuddy-tui）互相可见
- 同步完成度实测: IKUN 设备 completion=100%（synced）
- 双端清理测试泄漏的陈旧节点

## [0.4.0] - 2026-08-20 阶段四：实测反馈修复（文件浏览器/推送选择器/挂起弹窗等）

### 修复 —— 文件浏览器（用户实测反馈 #1/#8）
- **根因**: 路径经内联 onclick JS 字符串传递，反斜杠被解析吞掉
  （'C:\Users'→'C:Users'、'\b'→退格符 \x08）→ 相对路径拼到 data 目录 →
  "目录不存在: ...\data\小工具箱" / WinError 123
- **修复**: 全部改 data-* 属性 + 事件委托传路径（dataset 读取原样字符串）
- 新增**根视图盘符枚举**（Windows 全部硬盘/POSIX 根目录）——此前只能看到 C:
- 节点选择器切换即刷新（onchange → 根视图）
- 本机文件隐藏下载/整树下载按钮（本机文件已在硬盘中，无意义）
- 面包屑重构（data 属性传路径）；上一级支持 盘符根→根视图
- 后端 `_resolve_target_path` 控制字符防御（\x08 等直接拒绝并明确报错）
- 验证: scripts/verify_file_browser.py 14/14 PASS（IKUN 盘符 C:/E: 枚举、
  C:\Users/深层目录打开、单文件下载字节流+收件目录副本、选择器推送子目录
  保留结构、根目录默认收件目录、挂起含原因到期、控制字符防御）

### 新增 —— 推送文件选择器（用户反馈 #2）
- 侧栏"推送到当前浏览目录": 选择文件…（multiple）/ 选择文件夹…
  （webkitdirectory）两个文件选择器，替代手输绝对路径
- 文件夹推送保留目录结构（rel 相对路径逐文件推送，不经打包）
- 上传端点支持 rel 相对路径；浏览在根目录时推送落到对方统一收件目录（2.4.2）

### 修复/新增 —— 其他实测反馈
- **挂起弹窗**（#7）: prompt() 链改为正式模态（原因输入 + datetime-local 到期
  时间选择），恢复操作直接执行
- **mock 退役**（#3）: node_config.json 新增 enable_mock 开关（默认 true）；
  双端已设 false——两个 mock 是本机+IKUN 各一个（统一列表设计），使命完成已关闭，
  回归测试时改回 true 重启即可
- **心跳日志**（#5）: 新增每 5 分钟心跳摘要日志（mesh 连接数/对端名/运行时长），
  ping/pong 本身不落日志防刷屏；keepalive 修复后连接为长稳态（此前 15s 断连
  churn 才是日志只有几条的观感来源之一）
- **本机 WorkBuddy 执行器实测通过**（#4）: available=true，sync 任务提交 →
  deeplink+UIA 注入 → 结果文件回收 → 回复确认"本机链路正常"（透明路由 local）
- **IKUN WorkBuddy 复测**（连续两次成功，含 120s 注入重试）
- **msg_id 信封去重补齐**（设计 2.13.4）: handle_envelope 按 sender+msg_id
  LRU 去重，重复消息丢弃并记日志（此前仅 beacon 去重）
- 设置页"未保存"标记补全（输入变色、保存消除，2.9.9）
- 双端 known_peers 清理 48 个本地测试泄漏的陈旧节点（beacon 三路送达后
  本地测试实例会被远端看到——测试环境特有；面板"忘记节点"批量清理）
- mihomo 证据（#6）: mihomo.exe 运行中；IKUN→本机 41840 直连 0.042s（毫秒级
  = 局域网直连未绕代理）；mesh/beacon/文件/任务/同步全链路正常 = 直连规则生效

## [0.3.0] - 2026-08-20 阶段三：双机全能力验收通过（23/23）+ WorkBuddy 实调成功

### 双机部署（本机 Admin-PC ↔ IKUN-PC DESKTOP-F9P3UKK）
- IKUN 部署: ssh 推送 + tar 解压 + pip（Python 3.14: fastapi/uvicorn/pywinauto 全兼容）
- IKUN 节点经**计划任务（Interactive）在用户桌面会话运行**（session 1——UIA 驱动
  WorkBuddy 的硬前提；ssh 直接启动在 session 0 无桌面，UIA 不可用）
- 双端固定 peer_tcp_port=41840；防火墙按程序放行（python/pythonw/syncthing）
- Syncthing v1.29.4 二进制随包分发双端

### 阶段三关键修复（双机实测暴露）
- **WorkBuddy「覆盖当前草稿？」模态**: 上次未发送内容 + 新 deeplink 触发模态，
  模态期间 Edit 不可达 → 注入循环每轮先点掉模态再找输入框
- **comtypes COM 线程亲和**: pywinauto 首次导入线程结束后，新工作线程 COM 未初始化
  → UIA 全静默失败（表现为"找不到 Edit 控件"）→ 连接窗口前确保当前线程 CoInitialize
- **提交成功判定**: 预填后输入框本身含任务短码 → 改双条件（对话区含短码 且 输入框已清空）
- **注入整体重试 120s**: Electron UI 线程忙时 UIA 附加超时为瞬态（上次任务渲染中）
- keepalive ping 的 msg_id 与追踪 pid 分离导致 pong 永不匹配 → 15 秒必断 churn
  （本地测试因操作落在存活窗口内而侥幸通过——真实双机暴露）
- 255.255.255.255 广播部分 Windows 不收（实测定向 UDP/子网广播可达）→
  beacon 三路送达: 全网广播 + 子网定向广播(192.168.x.255) + 已知对端单播
- Syncthing REST: 列表端点 PUT 需数组 → 按 id 端点；设备地址加显式 tcp://IP:22000
- known_peers upsert 空值覆盖（握手回调 name=None 抹掉 beacon 名称）→ COALESCE
- 异步回执重试循环逻辑颠倒（到期才重试）→ 每 20s 重试至 deadline
- uvicorn 默认日志配置在 Python 3.14 报错 → log_config=None

### 验收结果（scripts/test_dual_machine.py --with-workbuddy: 23/23 PASS）
- 发现/连接: beacon 自动发现 + mesh 握手 + 能力广播（含 workbuddy）+ 在线检测
- 聊天: 双端落库；远程命令: echo 回显；开关拒绝路径（此前测试覆盖）
- 文件: push（SHA-256 校验+内容回读）/pull/远程目录列表/附件直传到任务工作目录
- 执行器: 远程深态查询（workbuddy available/idle）
- 任务: sync/async（回执进 caller 私有邮箱）/trigger（触发确认）/task_id 幂等
- **WorkBuddy 实调**: 本机提交 → IKUN 适配器 deeplink+UIA 注入 → WorkBuddy 执行 →
  结果文件契约回收 result.md → 回传本机（WorkBuddy 回复确认链路正常）
- 同步: 本机 data/sync 写入 → 25 秒内出现于 IKUN data/sync（beacon 自动互配生效）
- 通信日志 50+ 条审计；diag 自检全绿

## [0.2.0] - 2026-08-20 阶段二：双机部署与连通修复

- 双机 mesh 打通；beacon/keepalive/异步回执三处核心修复（详见 0.3.0 修复清单）
- IKUN 计划任务交互会话部署方案落地

## [0.1.0] - 2026-08-20 阶段一：核心施工 + 本地全量测试通过（47/47）

### 新增 —— transport 层（附录 A 线级协议）
- `transport/protocol.py`: 二进制帧（0x01 JSON 控制帧 / 0x02 文件块帧）、统一 JSON 信封
  （v/msg_id/type/sender_node_id/target_node_id/correlation_id）、8MiB 帧上限、SHA-256 工具
- `transport/beacon.py`: UDP 多端口冗余广播+监听、node_id+seq 去重、beacon 载荷组装（2.16.2）
- `transport/mesh.py`: 全连接长连接、首帧 ping 身份交换 + team 隔离在连接层强制（A.1）、
  去双连接（较小 node_id 发起者胜出；**淘汰连接延迟排空，在途文件传输不中断**）、
  5s ping/pong 保活、断线自动重连（2.13.3）、请求-响应（correlation_id 匹配）、
  文件分帧传输（A.4：file_push/file_pull/file_ack/file_result，多路复用 + 孤儿块缓存重放）
  、失败半写清理（2.17.5）

### 新增 —— node 层
- `node/config.py`: node_config.json 原子读写、node_id 自动生成、环境变量覆盖（2.14.4）
- `node/store.py`: comm.db 单库多表（通信日志/inbox/known peers/聊天），WAL+事务（2.17.5）
- `node/a2a.py`: A2A v1.0 数据形状（camelCase/JSON-RPC 2.0/ISO 8601）—— 按修订后 2.16.1
  复用数据模型而非引入 SDK 依赖（Python 3.14 目标机兼容性）
- `node/core.py`: 节点核心 —— 信封分发（线程池隔离阻塞操作）、三开关管辖（2.9.4）、
  任务三模式 sync/async/trigger（2.7）、异步回执邮箱+caller_id 归属（2.6）、
  本机异步直入邮箱、在线检测（beacon 超时+TCP 探测 3 次，2.1.6）、manual_peers（2.1.10）、
  单实例保护+异常退出告警（2.11.3/2.12.2）、diag 自检（2.17.7）
- `node/main.py`: 节点入口

### 新增 —— executors 层
- `executors/base.py`: 2.2.15 定稿签名（PluginContext/CapabilityResult/TaskInput/SubmitResult/
  ExecutorStatus/ResultPayload/ExecutorPlugin + 文件默认 is_done/get_result）
- `executors/registry.py`: 插件注册表 —— mock/内置适配器/配置 CLI 条目/data/plugins 外部插件、
  能力自检→广播（2.2.12）、结果文件契约轮询（存在+大小稳定 N 轮，2.2.11）、
  非交互 CLI 忙拒（不排队）/TUI·GUI 排队（2.13.2）、挂起门控+到期自动恢复+落盘（4.3/2.14.7）
- `executors/mock_plugin.py`: 测试桩（第五章 #4）
- `executors/cli_executor.py`: 非交互 CLI 基座（stdin 注入提示词防 argv 截断、可见控制台窗口、
  完成停留数秒；psmux 已装则优先）
- `executors/adapters/workbuddy.py`: WorkBuddy GUI 适配器 —— 复用 agent-bus 已验证的
  deeplink+UIA 注入/回收核心（2.2.8），主路=结果文件契约、兜底=UIA 文本回收、
  草稿覆盖弹窗自愈、能力自检（运行中→可用；已安装未运行→提示启动）

### 新增 —— 面板 / MCP / CLI / 同步
- `server/panel.py`: FastAPI REST（完整本地 API，127.0.0.1、5177 顺序兜底、panel.url）
- `server/static/index.html`: 七视图 SPA（概览/节点/消息/文件/执行器/日志/设置）+
  顶部状态灯 + 3s 局部轮询 + 开关即切即生效 + 保存按钮语义 + 危险操作确认（2.9.9/第四章）
- `mcp/server.py`: MCP stdio 薄桥（30 工具=2.5.9 ①~⑧；caller_id 环境变量/身份文件 2.6.3/2.6.4）
- `tools/cli.py`: 语义化子命令（register/list/send/task/check/inbox/upload/download/ls/shell/sync/diag）
- `sync/syncthing.py`: Syncthing 子进程托管 + REST 控制 + 同 team beacon 自动互配 +
  data/sync/ 文件夹 + sync_now（2.4.7；纯局域网配置：关全局发现/中继）

### 新增 —— 测试（47 个，全部通过）
- 单元: 协议帧/信封/文件块（test_protocol）、comm.db 四表语义（test_store）、
  注册表生命周期/忙拒/排队/挂起/超时/外部插件（test_registry）
- 集成: 同进程双节点 —— mesh 握手/聊天/shell（含开关拒绝）/文件 push·pull（SHA-256 校验、
  附件直传）/list_dir/执行器深态/任务 sync·async·trigger/幂等/面板 REST 全栈/team 隔离/diag
  （test_integration）
- MCP: stdio 协议握手/工具清单/提交任务→caller 私有邮箱隔离（test_mcp）
- CLI 执行器: fake_cli 端到端（stdin 完整性）（test_cli_executor）

### 修复的关键问题
- 去双连接淘汰会在途文件传输中断 → 延迟排空 + retired 标记（不误触发重连）
- 淘汰连接关闭误判断线 → 无限重连 churn → retired 连接不重连不通知
- pull 方向块帧先于接收器注册到达 → 孤儿块缓存重放
- 本机异步任务回执未入邮箱（只处理远程推送路径）
- CLI 命令带引号时 which 自检失败；tasklist 中文系统 GBK 解码崩溃（字节匹配）
- mesh 监听端口在构造时固化 → start 时读配置；节点停止未关 SQLite/日志句柄（Windows 文件锁）
