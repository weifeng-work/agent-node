# Changelog

本项目按 [重构设计方案.md](docs/重构设计方案.md) 施工，每阶段记录于此。

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
