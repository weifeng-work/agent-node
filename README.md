# Agent Node

局域网多机智能体协作节点。每台电脑部署一个，即可组建「多机多智能体协作团队」：自动局域网互发现、对称受控/遥控执行命令、系统状态托盘可见、跨节点文件收发、内嵌 MCP Server 供 AI 客户端调用。

> 从 `agent-bus` 仓库剥离出的**最小化独立项目**。源码运行，无打包交付。

---

## 一、能力设计

| 能力 | 说明 |
|------|------|
| 对称控制节点 | 每台电脑跑同一程序，功能等价，可互相遥控执行命令 |
| 自动局域网互发现 | UDP 广播 beacon（像 LocalSend），节点启动后自动发现同网段其他节点 |
| 受控能力 | 接收任意节点发来的 `shell_exec` 指令，在本机执行命令并回执；托盘可一键关闭（熔断） |
| 遥控能力 | 对任一发现的节点发起「在此电脑执行命令」，取回执行结果 |
| 状态可见性 | 托盘图标变色（有节点在线=绿，无=灰），标题实时显示发现的节点数 |
| 节点命名 | 默认取 hostname，可在 Web 面板随时改名，便于人类记忆 |
| 跨节点文件收发 | `fetch_file`（主动拉取）/ `post_file`（推送文件/目录，落对方收件箱） |
| 内嵌 MCP Server | 10 个标准 MCP 工具，供 Claude Desktop / Cursor 等 AI 客户端连接 |
| 中心角色（hub） | 额外起 MQTT broker + Web 面板 + 文件服务，成为团队中心 |
| 端口兜底 | MQTT / HTTP / 发现端口各备端口池，被占用时自动回退 |

## 二、使用场景

- **多机多智能体协作**：一个 AI 客户端通过 MCP 把多台电脑的能力封装为工具，跨机调度。
- **局域网远程运维 / 调试**：在任意节点向其他节点下发命令并取回结果。
- **跨机文件传输**：单文件 / 目录（自动打包 zip）双向收发。
- **AI 客户端接入**：把「发任务 / 查收件箱 / 执行命令 / 读写文件 / 上传下载」封装成标准工具给 Claude / Cursor 调用。

## 三、技术架构

```
┌──────────── 中心节点 (一台, --role hub) ────────────┐
│  mosquitto (MQTT broker)                           │
│  bus_server (FastAPI + SQLite, Web 面板 + 文件服务) │
│  自身控制节点 (带全部功能)                            │
└────────────────────────────────────────────────────┘
          ▲ MQTT / HTTP               ▲ UDP beacon 发现
          │                           │
┌─────────┴─────────┐       ┌─────────┴─────────┐
│ worker (对称控制节点) │  ...  │ worker (对称控制节点) │
│ 连总线 + 受控/遥控    │       │ 连总线 + 受控/遥控    │
└───────────────────┘       └───────────────────┘
```

- **通信层**：MQTT（mosquitto），消息按 topic 路由（`agent/<id>/inbox`、`bus/register`、`bus/heartbeat/`），QoS 1，retain 注册 + 遗嘱离线。
- **发现层**：UDP 广播 beacon（`255.255.255.255:41830`），对称无主从；worker 启动自动扫描 `is_master` 节点取真实 broker 地址（无需手动配置）。
- **文件层**：HTTP（bus_server，FastAPI），Claim-Check 上传下载，目录自动 zip / 安全解压（防路径穿越 + zip-slip）。
- **中心**：hub 角色内联启动 mosquitto（本机已装则复用，否则首启从 `build/mosquitto.zip` 解压）+ bus_server + Web 面板。
- **MCP**：FastMCP 走 stdio 传输，与 GUI 模式解耦，可独立启动。

**MCP 工具（10 个）**：`list_online_agents` · `send_task` · `check_inbox` · `run_command` · `read_file` · `upload_file` · `download_file` · `fetch_file` · `post_file` · `reply_task`

---

## 四、安装

### 4.1 前置

- **Python 3.10+**（所有机器）
- 局域网互通 + 防火墙放行（hub 侧入站）：
  - TCP `1883`（MQTT，端口池可能回退 `1884/1885/8883/18830`）
  - TCP `8000`（HTTP 面板/文件，池回退 `8001/8002/8010/8088`）
  - UDP `41830`（发现广播）

### 4.2 hub 主控机（一台）

需要**完整项目目录** + mosquitto。

```bash
# 1. 获取代码（git clone 或拷贝整个 agent-node 文件夹）
git clone <本仓库地址>

# 2. 安装依赖
cd agent-node
pip install -r requirements.txt

# 3. 启动中心节点（起 broker + Web 面板 + 自身控制节点 + 托盘）
python executor/control_app.py --role hub
```

- **mosquitto**：本机已装则自动复用（`shutil.which("mosquitto")` 或 `C:\Program Files\mosquitto`）；没装则首次启动自动从 `build/mosquitto.zip` 解压到 `data/runtime/mosquitto`，**所以 `build/` 必须保留**。
- **Web 面板**：启动后浏览器打开 `http://127.0.0.1:8000/`（端口池回退时看启动日志实际端口）。
- hub 首次启动会创建 `data/`（bus.db、files、broker.log、node_config.json），无需预置。

### 4.3 worker 其他机器（若干）

**只需精简子集**：`agent_bus/` 包 + `executor/control_app.py` + `requirements.txt`（`server/`、`build/`、`scripts/` 都不需要）。

```bash
# 1. 拷贝精简子集到目标机器（或 git clone 后仅运行，多余的目录会被自动忽略）
#    agent_bus/  executor/control_app.py  requirements.txt

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动 worker（无参数，自动扫描局域网中心）
python executor/control_app.py
```

- **无需配置 broker 地址**：worker 无 `--broker-host` 时自动扫描局域网 `is_master` 节点取真实地址（host / MQTT 端口 / HTTP 端口三样全取）。
- 手动指定中心（跨网段 / 自动发现失败时）：`python executor/control_app.py --broker-host 192.168.1.10`
- 收件箱自动落在 `Downloads/agent-node/inbox`，`data/` 配置目录自动生成，均无需预置。

### 4.4 精简文件清单

| 文件/目录 | hub | worker | 说明 |
|---|---|---|---|
| `agent_bus/` | ✅ | ✅ | 通信 SDK |
| `executor/control_app.py` | ✅ | ✅ | 唯一入口 |
| `requirements.txt` | ✅ | ✅ | 装依赖 |
| `server/`（含 `static/index.html`） | ✅ | ❌ | Web 面板，仅 hub 用 |
| `build/mosquitto.zip` | ✅ | ❌ | broker 便携包，仅 hub 用 |
| `scripts/` | 可选 | ❌ | 打包工具，运行时不用 |
| `data/` | 自动生成 | 自动生成 | 运行时产物 |

---

## 五、命令参数

| 参数 | 说明 | 默认 |
|------|------|------|
| `--role {worker,hub}` | 节点角色 | worker |
| `--agent-id` | 节点 ID（寻址身份） | `ctl-<hostname>` |
| `--name` | 显示名 | 同 agent-id |
| `--broker-host` | 连外部中心 broker 地址（worker 用；缺省自动发现） | 空 |
| `--broker-port` | MQTT 端口 | 池内自动选 |
| `--http-port` | HTTP 端口 | 池内自动选 |
| `--discovery-port` | 发现端口 | 池内自动选 |
| `--no-control` | 启动时关闭受控能力 | false |
| `--headless` | 不起托盘（后台/测试） | false |
| `--mcp` | MCP Server 模式（stdio） | false |

**端口池**

- MQTT：`1883 → 1884 → 1885 → 8883 → 18830`
- HTTP：`8000 → 8001 → 8002 → 8010 → 8088`
- 发现：`41830 → 41831 → 41832 → 41840 → 41850`

---

## 六、MCP 配置（给 AI 客户端）

`--mcp` 走 stdin/stdout，供 Claude Desktop / Cursor 等作为 MCP Client 通过 JSON 配置拉起。

```json
{
  "mcpServers": {
    "agent-node": {
      "command": "python",
      "args": [
        "C:/path/to/agent-node/executor/control_app.py",
        "--mcp", "--role", "worker", "--agent-id", "my-agent"
      ],
      "env": { "BUS_BROKER_HOST": "192.168.1.10" }
    }
  }
}
```

> `BUS_BROKER_HOST` 可省略——worker 会自动发现中心；跨网段或发现失败时显式指定。

## 七、目录结构

```
agent-node/
├── executor/
│   └── control_app.py          # 节点主入口（对称 + 托盘 + MCP + 中心角色）
├── agent_bus/                  # 通信 SDK（client/config/discovery/provision/files/schema...）
├── server/
│   ├── bus_server.py           # 中心 HTTP + 消息服务（可编程 serve_bus_server 内联启动）
│   └── static/index.html       # Web 面板
├── scripts/
│   └── build_mosquitto_zip.py  # 重建 mosquitto 便携包
├── build/
│   └── mosquitto.zip           # 便携 mosquitto（hub 首启自解压）
├── docs/
│   ├── 需求与设计.md
│   └── BUGFIX_LOG_20260819.md
└── requirements.txt
```

## 八、安全模型

本项目定位于**可信局域网**：broker `allow_anonymous`，控制消息明文走 MQTT，仅做 `sender_id` 前缀白名单（控制面只接受 `hub-*` 身份）。**不做** HMAC 验签 / 加密 / 节点白名单——跨不可信网络需自行加固。
