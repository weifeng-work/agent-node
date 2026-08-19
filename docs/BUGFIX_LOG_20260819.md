# Bug 修复记录

> 项目：agent-node（局域网多机智能体协作节点）
> 修复执行时间：2026-08-19 01:50:15 ~ 01:57:49

---

## Bug 1 — client.py 缺少 Path 导入（严重）

- **时间戳**：2026-08-19 01:50:23
- **文件**：`agent_bus/client.py`
- **问题**：第 90 行 `Path(self.status_file)` 使用了 `Path`，但文件头部从未导入 `pathlib.Path`。当 `status_file` 非空时（设置 `BUS_STATUS_FILE` 环境变量即触发），`_write_status()` 抛 `NameError`，异常被外层 `except Exception` 静默吞掉，导致状态文件永远不更新。
- **修复**：在 imports 区域添加 `from pathlib import Path`。

## Bug 2 — --broker-host 参数在 GUI/headless 模式下被忽略（严重）

- **时间戳**：2026-08-19 01:50:31
- **文件**：`executor/control_app.py`
- **问题**：`ControlNode.run()` 中 `self._broker_host = "127.0.0.1"` 硬编码，忽略了命令行参数 `--broker-host`。导致 worker 节点在 GUI/headless 模式下永远连本地 broker，无法远程加入团队。MCP 模式（第 683 行）已正确使用参数，两处不一致。
- **修复**：改为 `self._broker_host = self.args.broker_host or "127.0.0.1"`，与 MCP 模式保持一致。

## Bug 3 — 文件下载 URL 双重前缀导致 404（严重）

- **时间戳**：2026-08-19 01:50:38
- **文件**：`agent_bus/files.py`、`server/bus_server.py`
- **问题**：hub 以编程方式启动 bus_server 时 `BUS_HTTP_BASE` 未设置，服务端返回相对路径 `/api/files/xxx`。客户端 `download_file` 不识别相对路径，当作 file_id 再包一层前缀，生成 `http://host/api/files/%2Fapi%2Ffiles%2Fxxx`，导致 404。文件传递链完全断裂。
- **修复**：在 `download_file` 中增加对 `/` 开头的相对路径的处理分支，拼接 `http_base + 相对路径` 生成正确 URL。

## Bug 4 — 节点移除 API 返回值与前端不匹配（中等）

- **时间戳**：2026-08-19 01:57:35
- **文件**：`server/bus_server.py`
- **问题**：`DELETE /api/admin/nodes/{agent_id}` 返回 `{"ok": True, "agent_id": ...}`，但前端 `index.html` 期望 `j.broker_restarted` 和 `j.broker_message` 字段。两个字段均为 `undefined`，导致每次移除节点都弹出 "已移除。注意: undefined" 的错误提示。
- **修复**：API 返回值补充 `broker_restarted: False` 和 `broker_message` 字段，与前端契约对齐。

## Bug 5 — hub 启动时 broker 端口未等待就绪（中等）

- **时间戳**：2026-08-19 01:57:42
- **文件**：`executor/control_app.py`
- **问题**：`start_center()` 启动 mosquitto 子进程后立即启动 bus_server（内含 MqttBridge 连接）和 `self.connect()`，没有等待 broker 端口 bind 就绪。首次启动时 MqttBridge 和控制节点的 MQTT 连接可能抢跑失败，靠 paho 自动重连恢复，但首次注册/心跳延迟且日志出现连接错误。
- **修复**：新增 `_wait_port_ready()` 静态方法（200ms 间隔轮询，默认 5s 超时），在 broker 子进程启动后调用等待端口就绪再继续。超时仅 warning 不阻塞，靠自动重连兜底。

---

## Bug 6 — pystray 菜单 grab 与 tkinter grab 冲突导致进程崩溃（严重）

- **时间戳**：2026-08-19 03:15:00
- **文件**：`executor/control_app.py`
- **问题**：托盘菜单「设置节点名…」触发 `_menu_rename` → `_ask_string` → `simpledialog.askstring(parent=root)`。`simpledialog.Dialog.__init__` 在第 144 行调用 `self.grab_set()` 时，**pystray 弹出菜单仍持有 Windows 窗口 grab**，抛出 `_tkinter.TclError: grab failed: another application has grab`。该异常未被捕获，传播过程中导致 Tcl 解释器内部状态损坏，触发 `Fatal Python error: PyEval_RestoreThread`，**整个进程崩溃退出**，对话框永远不出现。
- **堆栈**（来自 `stderr3.log`）：
  ```
  _menu_rename → _ask_string → simpledialog.askstring
    → Dialog.__init__ → self.grab_set()
    → _tkinter.TclError: grab failed: another application has grab
    → Fatal Python error: PyEval_RestoreThread (GIL corruption)
  ```
- **修复**：彻底移除 tkinter 依赖，改用 Windows 原生 API 实现输入框：
  1. **`_ask_string`** — 用 `CreateWindowExW` 创建原生对话框（STATIC + EDIT + BUTTON 控件），通过 `GetMessageW` + `IsDialogMessageW` 模态消息循环阻塞等待，`WM_COMMAND` 处理确定/取消
  2. **`_toast`** — 用 `MessageBoxW` 替代 `tkinter.messagebox.showinfo`
  3. 原生 API 不依赖 tkinter 的 Tcl 解释器，从根本上消除了 grab 冲突的可能性
  4. 同时移除了 `withdraw()` / `_keep_focus` / `root.after` 等所有 tkinter hack
- **影响范围**：`_ask_string` 被两个地方调用：① 设置节点名 ② 远程执行命令输入框，两处一并修复。

| # | 问题 | 文件 | 影响 |
|---|------|------|------|
| 7 | 心跳线程与 disconnect 竞态，状态文件可能短暂闪回 connected | client.py:176-182 | 断开后状态闪回几十毫秒 |
| 8 | on_message 回调处理后的消息仍入 _inbox 队列，MCP check_inbox 可能返回已处理消息 | client.py:169-174 | AI 可能重复处理，长期内存增长 |
| 9 | BeaconBroadcaster/ControlAdvertiser.stop() 不关闭 socket | discovery.py:102-103, 203-204 | socket 泄漏至进程结束 |

---

## Bug 7/8/9 修复落地 + Bug 10（2026-08-19 09:45~09:55）

> 本次同时完成了 exe 化退出（删除 PyInstaller 产物/e2e 脚本，转为纯源码运行）与全链路测试。

### Bug 7 — 心跳线程与 disconnect 竞态（已修复）

- **文件**：`agent_bus/client.py`
- **修复**：
  1. `_heartbeat_loop` 在 `publish` 后、`_write_status("connected")` 前二次检查 `_disconnected`，杜绝 stopped 之后闪回 connected
  2. `disconnect()` 置标志后先 `join` 心跳线程（2s 超时）再写 stopped
  3. **新增**：`_on_disconnect` 回调在主动断开（`_disconnected=True`）时直接返回，不写 `reconnecting`——此前 paho 的 DISCONNECT 回调会把 stopped 覆盖成 reconnecting

### Bug 8 — on_message 已处理消息重复入 inbox（已修复）

- **文件**：`agent_bus/client.py`、`executor/control_app.py`
- **修复**：`on_message` 回调改为返回 bool 语义——`True`=已消费，不再入 `_inbox`；`False`/无回调/回调异常均照常入队（不丢消息）。`ControlNode._on_message` 对 `shell_exec` 无论成败返回 `True`，避免 MCP `check_inbox` 重复返回同一条受控指令。

### Bug 9 — discovery socket 泄漏（已修复）

- **文件**：`agent_bus/discovery.py`
- **修复**：`BeaconBroadcaster.stop()` 与 `ControlAdvertiser.stop()` 关闭 `_sock` 并 `join` 线程（2s 超时）。

### Bug 10 — MCP 全部工具失效：node 闭包引用未定义（严重，本次测试发现）

- **时间戳**：2026-08-19 09:54
- **文件**：`executor/control_app.py` `EmbeddedMCPServer.tools()`
- **问题**：8 个 MCP 工具闭包内引用 `node`，但该变量仅存在于 `main()` 局部作用域，闭包词法作用域链只到模块级 → 所有工具调用抛 `NameError: name 'node' is not defined`，MCP 能力整体不可用（此前从未被测试到）。
- **修复**：工具闭包改为引用 `self.node`（捕获 `tools` 方法的 `self` 参数）。
- **验证**：MCP stdio 握手 + `list_tools` 8 工具齐全 + `call_tool(list_online_agents)` 真实返回在线节点列表。

### 工程变更（本轮）

- 删除 PyInstaller 产物：`dist/`、`_e2e_run/`、`build/control_app.spec`、`build/pyi`（保留 `build/mosquitto.zip` 内置 broker 包）
- 删除 tkinter 时代调试残留与 e2e 脚本：`scripts/e2e_tray_rename_test.py`、`_probe_menu_input.py`、`_repro_tray_tk.py`、`repro_trace.log`
- README 改为源码运行说明（`python executor/control_app.py`）
- 验证结果：Bug7/8/9 单测 + hub 冒烟 + MCP 冒烟 **17/17 + MCP 全通过**

---

## 策略变更：节点改名从托盘弹窗迁到网页面板（2026-08-19 10:29~10:45）

### Bug 11 — 原生输入弹窗在 pystray 线程下交互异常（弃用弹窗方案）

- **现象**：托盘「设置节点名」弹出窗口后，Edit 内文字无法选中，确定/取消按钮点击无效。
- **决策**：放弃原生弹窗方案（`_ask_string`）。改名迁移到网页面板，托盘「设置节点名…」改为打开本地面板。
- **影响**：`_ask_string` 仍被「远程执行命令」入口调用（未在本次策略范围内，若同样异常可另行处理）。

### 新链路：网页面板改名

1. **前端**（`server/static/index.html`）：节点 chip 增加 ✎ 按钮 → `prompt` 输入新名 → `POST /api/admin/rename`
2. **后端**（`server/bus_server.py`）：
   - `Store.rename_agent()`：立即更新中心名单
   - `MqttBridge.publish()`：新增发布能力
   - `POST /api/admin/rename`：目标为本 hub 自身 → 同进程回调 `ControlNode.set_name`（mode=local）；远程节点 → MQTT 下发 `payload.op="rename"` 指令（mode=remote）
3. **节点端**（`executor/control_app.py`）：
   - `_handle_rename()`：处理 rename 指令 → `set_name()` 持久化本地 config + 重发 register 刷新中心名单 + 回执
   - `_menu_rename` 改为跳转本地面板 `http://127.0.0.1:{http_port}/`
4. **验证**：hub 本地改名（mode=local）、worker 远程改名（mode=remote，MQTT 下发 → 本地 config 持久化 → 中心名单同步）、改名不存在节点不报错、rename 指令入消息时间线 → **10/10 通过**

---

## 新能力：跨节点文件收发 fetch_file / post_file（2026-08-19 10:57~11:25）

### 设计

- **收件箱目录**：`Downloads/agent-node/inbox`（Windows Known Folder 定位，回退 `~/Downloads`）。节点启动时幂等创建（`mkdir exist_ok`），**绝不删除/覆盖用户已有文件**。
- **fetch_file**：任意节点发 `op=fetch_file {path}` → 目标机校验存在（目录自动 zip 打包）→ 上传中心文件服务 → 回执 `{url, name, size, is_dir}` → 请求方 `download_file` 落盘。无 20KB 截断限制。
- **post_file**：发送方 upload（目录先 zip）→ 发 `op=post_file {urls, names, unpack}` → 目标机下载到 inbox（`basename` 防路径穿越）→ zip 安全解压（`is_relative_to` 防 zip-slip）→ 回执 `{saved, unpacked}`。
- **权限**：两者均受 `shell_control` 开关控制（与 shell_exec 同权限面）。
- **MCP 工具**：新增 `fetch_file(target_id, path, dest)`、`post_file(target_id, paths)`（10 工具）。

### 过程中修复的关联 bug

- **Bug 12（重要）— worker 文件服务地址指向自己未启动的 HTTP 服务**：`connect()` 原先 `http_base = http://{host}:{self.http_port}`，worker 不跑 bus_server，其 upload/download 全部打空（连接拒绝）。修复：hub 用自己端口；worker 角色通过 beacon 扫描中心（`is_master`）节点取真实 `http_port`，扫不到回退 8000。
- **Bug 13（重要）— hub `--mcp` 模式启动顺序错误**：`main()` 中 MCP 分支先 `connect()` 后 `start_center()`，broker 未起就连 MQTT → 必然连接失败（此前未被真实 MCP 端到端路径触达）。修复：hub 先 `start_center()` 再 `connect()`（与 GUI 模式顺序一致）。
- **Bug 14（次要）— upload 返回结构文档与实现不符**：`files.upload_file` 文档声称返回 `{file_id, name, size, url}`，实际 `bus_server` 只返回 `{file_id, url, size}`。修正文档，fetch_file 用本地路径名兜底。
- **Bug 15（测试发现）— MCP fetch_file 回执状态判断层级错误**：`status` 在 task_result 顶层，不在 `result` 子对象；判断改为 `res.get("status")`。

### 验证结果

- 端到端（真实 hub + worker 子进程 + 远程节点客户端）：**16/16 通过**——inbox 幂等（预置文件不删）、fetch 单文件内容一致、fetch 目录 zip 含顶层目录与嵌套文件、post 文件/目录落盘解压、路径穿越拦截（`../evil` 落盘在 inbox 内）、fetch 不存在文件返回 error。
- MCP 冒烟（hub `--mcp` + worker）：10 工具齐全，`fetch_file` 真实跨机拉取落盘，`post_file` 真实推送至对方 inbox → **全通过**。

---

## 第二轮审查 + 修复（2026-08-19 12:20~12:35）

> 在第一轮 Bug 1-15 基础上深度代码审查，发现 Bug 16-36 共 16 处。
> 严重 5 处、中等 8 处、已记录未修复 1 处、设计问题 2 处、文档/死代码若干。

### Bug 16 — hub 退出后 mosquitto 子进程变孤儿（严重）
- **文件**：`executor/control_app.py` `shutdown()`
- **问题**：`shutdown()` 只停 `bus` 和 `_advertiser`，未停 `broker_proc`（mosquitto 子进程）和 `_uv_server`（uvicorn daemon 线程）；Popen 未绑 Job Object，hub 退出后 mosquitto 残留系统，多次启停累积孤儿进程。
- **修复**：`shutdown()` 内增加 `uv_server.should_exit=True` + `bridge.stop()` + `broker_proc.terminate()` + `wait(timeout=2)` + 失败回退 `kill()`。

### Bug 17 — 删节点后 MQTT retain register 重新激活节点（严重）
- **文件**：`server/bus_server.py` `Store` / `MqttBridge._on_message` / `remove_node`
- **问题**：DELETE agents 后，bridge 重连订阅 `bus/#` 时 broker 投递 retain register 消息 → `upsert_agent` → 已删节点"复活"。`bus/register` 是共享 retain topic，无法清单个节点。
- **修复**：Store 增加 in-memory tombstone dict（agent_id → 删除时间戳，TTL 60s）；bridge 收到 register 时检查 tombstone，TTL 内忽略；TTL 过期允许节点重新 register（重启场景）。

### Bug 18 — rename 不存在节点返回 ok=True（中等）
- **文件**：`server/bus_server.py` `Store.rename_agent` / `rename_node` 路由
- **问题**：`UPDATE agents SET name=? WHERE agent_id=?` 0 行更新也不报错，路由返回 `ok=True`，前端误以为改名成功。
- **修复**：`rename_agent` 返回 `cur.rowcount`；路由检查 `affected==0` 抛 `HTTPException(404, "agent 不存在")`。

### Bug 19 — MCP `post_file` 不检查 task_result status（严重）
- **文件**：`executor/control_app.py` `EmbeddedMCPServer.tools().post_file`
- **问题**：`res = send_msg(wait=True)` 后直接 `return json.dumps(res.get("result") or {})`，error 状态时 result 子对象可能为空 `{}`，AI 收到空 dict 误以为推送成功。
- **修复**：检查 `res.get("status") != "success"` 时返回 `{"error": err, "status": ...}`，error 时也透传 `_handle_post_file` 的 partial saved 列表。

### Bug 20 — post_file 异常丢失 saved 列表（中等）
- **文件**：`executor/control_app.py` `_handle_post_file`
- **问题**：异常时只发 `error=str(e)`，不带已落盘的 `saved` 列表；发起方收到 error 误以为全失败，重发导致 inbox 重复。
- **修复**：异常路径 `_reply(status="error", output_text=partial_json)`，partial_json 含 `{saved, unpacked, error}`。

### Bug 22 — safe_unzip 整文件读到内存（严重）
- **文件**：`executor/control_app.py` `safe_unzip`
- **问题**：`d.write(s.read())` 整个文件读到 bytes 再写盘，几百 MB 单文件 zip 会爆 OOM。
- **修复**：改为 64KB chunked 读写循环。

### Bug 23 — download 失败半下载文件不清理（中等）
- **文件**：`agent_bus/files.py` `download_file`
- **问题**：`open(dest, "wb")` + `iter_content` 写盘，中途异常 dest 留下残破文件。
- **修复**：先写 `dest + ".part"` 临时文件，成功 `os.replace` 到 dest，失败 `unlink(tmp)` 清理。

### Bug 24 — upload 同步 IO 阻塞事件循环（严重）
- **文件**：`server/bus_server.py` `/api/files/upload` 路由
- **问题**：`async def upload` 内 `open(dest,"wb")` + `out.write(chunk)` 是同步 IO，大文件上传时阻塞事件循环，影响其他请求。
- **修复**：写盘丢工作线程，`await anyio.to_thread.run_sync(out.write, chunk)`；读 chunk 仍是 async。

### Bug 25 — MCP `run_command` 绕过 shell_control 开关（中等）
- **文件**：`executor/control_app.py` `EmbeddedMCPServer.tools().run_command`
- **问题**：托盘"关闭受控能力"只挡远程 `shell_exec`，AI 经 MCP `run_command` 仍可任意执行命令——开关形同虚设。
- **修复**：`run_command` 入口检查 `self.node.shell_control`，关闭时返 `{"error": "shell_control_disabled"}`。

### Bug 26 — discovery socket 竞态泄漏（中等）
- **文件**：`agent_bus/discovery.py` `BeaconBroadcaster` / `ControlAdvertiser`
- **问题**：`_sock` 在 `_run()` 线程内创建，`stop()` 可能在 `_run` 创建 sock 之前调用，`if self._sock is not None` 跳过 close，sock 创建后无人关闭 → 泄漏。
- **修复**：sock 创建移到 `start()` 同步执行并设 SO_BROADCAST；`_run` 直接进循环；`sendto` 失败时 break 退出（sock 已被 stop close）。

### Bug 27 — scan_control_nodes fallback bind(0) 收不到广播（设计问题）
- **文件**：`agent_bus/discovery.py` `scan_control_nodes`
- **问题**：`bind(("", 0))` 落到随机端口，广播发往 DISCOVERY_PORT，bind 在随机端口收不到 → 扫描静默失效。原代码注释自承认"回落广播收不到？"
- **修复**：改为遍历 `[discovery_port] + DISCOVERY_PORT_POOL` 找可绑端口；都不可用才返回 `[]`。

### Bug 28 — SHGetKnownFolderPath 内存泄漏（中等）
- **文件**：`executor/control_app.py` `downloads_dir`
- **问题**：`SHGetKnownFolderPath` 返回的 PWSTR 由 CoTaskMem 分配，调用方必须 `CoTaskMemFree` 释放；原代码漏 free，每次初始化 inbox 都泄漏一次小缓冲。
- **修复**：加载 `ole32.CoTaskMemFree`（设 argtypes/restype），取出 `p.value` 后 `CoTaskMemFree(p)` 释放原指针。

### Bug 11/29 — _ask_string 64 位指针截断（已记录未修复，本次落地）
- **文件**：`executor/control_app.py` `_ask_string`
- **问题**：BUGFIX_LOG Bug 11 已弃用此弹窗（"设置节点名"切到网页面板），但 `_menu_exec`（远程执行命令入口）仍调用；64 位 Python 下 user32 函数未设 argtypes/restype，HWND/HANDLE 指针默认 c_int 截断，可能句柄错位崩溃。
- **修复**：补全 16 个 user32/kernel32 函数的 argtypes + restype（GetModuleHandleW/RegisterClassExW/CreateWindowExW/DestroyWindow/PostQuitMessage/DefWindowProcW/GetDlgItemTextW/LoadCursorW/GetSystemMetrics/SetFocus/SendMessageW/GetMessageW/IsDialogMessageW/TranslateMessage/DispatchMessageW/GetLastError）。

### Bug 31 — docstring 错别字（文档）
- **文件**：`executor/control_app.py` `list_online_agents` docstring
- **问题**："查看总线上一在线智能体"应为"查看总线上在线智能体"。
- **修复**：改"上一"为"上"。

### Bug 32 — MqttBridge 缺 on_disconnect 日志（中等）
- **文件**：`server/bus_server.py` `MqttBridge`
- **问题**：未实现 `on_disconnect` 回调，broker 抖动重连时无日志，排查困难。
- **修复**：增加 `_on_disconnect` 回调，记 warning 含 reason_code。

### Bug 33 — MqttBridge.publish 不检查返回值（中等）
- **文件**：`server/bus_server.py` `MqttBridge.publish`
- **问题**：`info.wait_for_publish(timeout=5)` 返回值被丢弃，未连接时 publish 排队超时静默失败，调用方（改名下发）以为已发。
- **修复**：`publish` 返回 `bool`，超时/未连接时 log warning 含 topic/rc/mid。

### Bug 36 — fetch_file 不拦截 symlink 越权（安全）
- **文件**：`executor/control_app.py` `_handle_fetch_file`
- **问题**：`Path.exists()` 跟随符号链接，攻击者可用 symlink 指向目录外敏感文件（如 `~/.ssh/id_rsa`）越权读取。
- **修复**：`is_symlink()` 检查，符号链接一律拒绝返 `error="拒绝读取符号链接"`。

### 死代码清理（pyflakes 警告）
- `server/bus_server.py`：删未用 `from agent_bus.discovery import BeaconBroadcaster, PROTO, PROTO_VER`。
- `executor/control_app.py`：删未用 `from agent_bus import schema`；删 `tools()` 内未用 `from mcp.server.fastmcp import FastMCP`。
- `scripts/build_mosquitto_zip.py`：删未用 `import sys`。

### 验证
- `py_compile` 全部 12 个 .py 文件 OK
- `pyflakes` 全部 0 警告
- 模块独立导入测试全通过
- dry-run 关键修复点：
  - Bug 17 tombstone 拦截 retain register ✓
  - Bug 18 rename 不存在 agent 返 rowcount=0 ✓
  - Bug 22 safe_unzip chunked 写 200KB 文件 ✓
  - Bug 23 download 失败清理 .part，dest 不残留 ✓
  - Bug 26 BeaconBroadcaster/ControlAdvertiser start/stop sock 安全 close ✓
  - Bug 27 scan_control_nodes 端口池兜底返回 [] ✓
  - Bug 28 downloads_dir 正常返回 Path ✓
  - Bug 11/29 _ask_string 内 16 处 argtypes + 16 处 restype 全设 ✓

### 未修复（设计问题，需产品决策）
- ~~**Bug 30**~~：已实现，见下。
- ~~**Bug 35**~~：已修，见下。

---

## Bug 30 + 35 修复（2026-08-19 12:39）

> 用户确认：worker 自动发现中心是理想产品形态，应当实现；Bug 35 按建议兜底。

### Bug 30 — worker 启动自动扫描 is_master 取真实 broker_host（已实现）
- **文件**：`executor/control_app.py`
- **改动**：
  1. `_beacon()` 拆分出 `_beacon_host()`：hub 广播自己的局域网 IP（取 `local_ips()` 第一个非 127.0.0.1），worker 广播自己连接的中心地址。**修复根因**——此前 hub 广播 `mqtt_host=127.0.0.1`，worker 扫到后会连到自己。
  2. 新增 `_discover_center()`：`scan_control_nodes` 扫 is_master 节点，用 `host_ip`（广播源地址，天然可达）而非自报 `mqtt_host`，返回 `(broker_host, mqtt_port, http_port)` 三元组；扫不到返 None。
  3. `connect()` 重构地址解析：hub→本地 127.0.0.1+自身端口；worker+显式 `--broker-host`→该 host+自身 mqtt_port+扫中心 http_port；worker+无显式 host→自动发现三样全取，回退本地并 log 提示。
  4. `run()` 与 `main()` MCP 分支不再预塞 `127.0.0.1` 给 connect，改为 `connect(self.args.broker_host or None)`。
- **验证**：monkeypatch `_discover_center` 返假中心 `('192.168.1.10', 1884, 8010)`，worker connect 后 `_broker_host/mqtt_port/http_port` 三样正确；hub 路径 `127.0.0.1:1883:8000` 正确；`_beacon_host` hub 返局域网 IP、worker 返中心地址。

### Bug 35 — upload filename None 兜底（已修）
- **文件**：`server/bus_server.py` `/api/files/upload`
- **改动**：`fname = file.filename or "unnamed"`，落盘路径、`store.add_file`、日志统一用 `fname`，避免 filename=None 时 SQLite 存 NULL、前端展示 "None"。

### 验证
- `py_compile` 12 文件全 OK，`pyflakes` 0 警告。
- Bug 30 dry-run：worker 自动发现三样端口正确、hub 本地路径正确、`_beacon_host` 语义正确。
- 至此 Bug 16-36 全部处理完毕。

