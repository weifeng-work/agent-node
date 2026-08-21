# agent-node 节点协作 SKILL 说明书

> 本文件是智能体（AI）使用 agent-node 节点能力的**唯一官方说明书**。
> 架构与接口已稳定（2026-08-20）。加载本 skill 即学会与本机节点及全网节点协作。
> 提示：AI 也可经 MCP 工具 `get_skill` 重新读取本手册全文；深度架构/协议文档在
> `docs/`，用 `get_skill(doc=<文档名>)`（如 协议与架构 / 重构设计方案）按需读取。

## 一、这是什么

agent-node 是局域网多机智能体协作节点。每台电脑跑一个节点，节点间自动发现、
点对点直连（无中心服务器）。你（AI）通过**本机节点**获得三样能力：

1. **调用其他电脑上的智能体**（执行器：CodeBuddy CLI/TUI、WorkBuddy 等）
2. **跨机文件传输**（推送/拉取/浏览任意目录）
3. **跨机聊天与远程命令**

> **安装**（无 Node，Windows）：PowerShell 一行 `irm
> https://raw.githubusercontent.com/weifeng-work/agent-node/main/scripts/install.ps1 | iex`
> ——自动下源码 + venv 装依赖 + 建桌面快捷方式 + 启动节点。日常控制用
> `agent-node start|stop|status|restart|update|mcp`。

## 二、两种接入方式（能力等价）

### 方式 A: MCP（推荐，支持 MCP 的智能体）

MCP 客户端 JSON 配置：

```json
{
  "mcpServers": {
    "agent-node": {
      "command": "C:/Users/YOUR_USERNAME/AppData/Local/agent-node/venv/Scripts/python.exe",
      "args": ["-m", "mcp.server"],
      "cwd": "C:/Users/YOUR_USERNAME/AppData/Local/agent-node/app",
      "env": {
        "AGENT_NODE_PANEL": "http://127.0.0.1:5177"
      }
    }
  }
}
```

> `command` 一定是 **venv 里的 python**（`%LOCALAPPDATA%\agent-node\venv\Scripts\python.exe`，
> 依赖装在那里；不要写成系统 `python`）。直接运行 `agent-node mcp` 会打印带本书本机真实路径
> 的完整 MCP JSON。`AGENT_NODE_PANEL` 的 `5177` 是**默认**面板端口，若被占用节点会自动改用
> 5178…，实际以 `agent-node status` 显示为准。

**caller_id 是你的身份**（MCP server 进程身份，随每次工具调用自动注入，
你无需记住它）。**caller_id 对应你的专属收件箱**——异步任务回执只进你的邮箱，
其他 AI 客户端看不到。caller_id 由系统在 MCP server 启动时读取**父进程可执行名**
（如 workbuddy/trae）自动派生：同一客户端重启后 id 不变、不同客户端 id 不同、
本机不冲突，**无需配置**。

### 方式 B: CLI（不支持 MCP 但有 Bash 能力的智能体）

> 先用「无 Node 一键安装」装好的用户，日常用 `agent-node` 命令：
> `agent-node start|stop|status|restart|update|mcp`（mcp 会打印真实 MCP 配置）。
> 下面是**源码/开发形态**的 CLI（`tools/cli.py`，需在仓库目录里跑）：

```bash
cd %LOCALAPPDATA%\agent-node\app   # 到节点安装目录（默认；以你机器实际为准）
python tools/cli.py register          # 首次: 生成 caller_id 身份文件
python tools/cli.py list              # 在线节点列表（仅显示当前在线对端）
python tools/cli.py task --target <node_id> --executor <agent_id> \
    --prompt "任务指令" --mode async   # 发任务（--mode sync|async|trigger）
python tools/cli.py inbox             # 取异步回执（caller_id 自动注入）
python tools/cli.py anchor add <host> <对方对等TCP端口>   # 入站被隔离时配锚点自愈
```

环境变量: `AGENT_NODE_PANEL`（面板地址，默认 http://127.0.0.1:5177）。
caller_id 无需配置（MCP 自动派生；CLI 用 `cli.py register` 生成身份）。

## 三、核心概念（防混淆）

| 标识 | 含义 |
|---|---|
| `node_id` | 节点唯一身份（`node-<主机名>-<短码>`），**寻址一律用它** |
| `name` | 节点显示名（人改的，可重复，仅展示） |
| `agent_id` | 执行器条目在本节点的标识（如 `codebuddy-cli`/`workbuddy`） |
| `executor_id` | **全局寻址** = `<node_id>/<agent_id>`，AI 用它调执行器 |
| `caller_id` | **你的**身份（收件箱归属键，自动注入） |

## 四、常用工作流

### 调用远程智能体

```
1. list_executors()                  → 拿到所有可用执行器（含本机+远程）
2. submit_task(executor_id, prompt, mode=...)  → 提交任务
3a. mode=sync   → 调用阻塞，直接拿结果（简单任务）
3b. mode=async  → 立即返回，继续干别的；回执进你的邮箱，之后 check_mail() 取
3c. mode=trigger → 只要"触发成功"确认，不要结果
```

**三模式中文对照**（人类用中文指示，你据此映射到 mode 值）：

| 中文说法 | mode 值 | 行为 |
|---|---|---|
| 同步调用 | `sync` | 阻塞等结果，一次拿到 |
| 异步调用 | `async` | 立即返回，结果回执进你的异步邮箱，稍后 `check_mail()` 取 |
| 仅触达 | `trigger` | 只要"触发成功"确认，不要结果 |

**使用建议**：简单快任务用同步调用（sync）；长任务/可能很久才完成用异步调用（async，别阻塞等待）；只要对方开始执行、不需要结果时用仅触达（trigger）。**异步调用的回执可能很久才到**（例如执行器明天才回复）——发出后即可继续干别的，回执到达会有通知，之后 `check_mail()` 取即可。

**幂等重试**：调用失败重发时传同一 `task_id`，目标节点不会重复执行。

### 跨机文件

```
file_push(node_id, local_path, target_path?)   # 推送（默认落对方 data/inbox/）
file_push_dir(node_id, local_root, target_base?)   # 整目录树推送（免打包，自动排除 venv/data/node_modules 等；默认落对方 data/inbox/<相对路径>/）
file_pull(node_id, path)                        # 拉取（默认落本机 data/inbox/）
list_dir(node_id, path, recursive?)             # 浏览对方任意目录
sync_now()                                      # 触发 data/sync/ 同步目录扫描
```

### 远程命令

```
shell_exec(target_node, command, timeout?)      # 受目标节点 allow_shell 开关管辖
```

## 五、错误码（结构化失败，按此决策）

| 错误码 | 含义 | 你的应对 |
|---|---|---|
| `offline` | 目标节点不在线 | 稍后重试或告知人类 |
| `disabled` | 目标节点对应开关关闭（allow_shell/file/ai_task） | 告知人类去目标机开开关 |
| `not_installed` | 执行器不可用（未装/未运行） | 换执行器或告知人类 |
| `busy` | 非交互执行器满并发（不排队） | 稍后重试 |
| `suspended` | 执行器被人类挂起（如额度用尽） | 告知人类，别硬试 |
| `timeout` | 超时 | 可带同一 task_id 重试 |
| `agent_error` | 智能体本身报错 | 读 detail 判断 |

## 六、行为边界（必须遵守）

**允许**：
- 经标准接口（MCP 工具 / cli.py）操作节点与本网其他节点
- 读取节点运行状况与日志（get_node_log / get_comm_log）
- 经 `distribute_plugin` 分发执行器插件、`restart_plugin` 重启插件
- 经 `restart_node` **重启本机节点进程**（保持驻留，面板短暂中断后自动恢复）

**禁止**：
- **修改任何节点源码 / 打补丁 / 改配置文件**（擅自改动会让该节点行为与其他
  节点不一致，破坏协同）
- **关闭 / 卸载 / 停止驻留节点进程**（节点需保持驻留；仅允许保持驻留的**重启**，
  且只经 `restart_node` / cli.py，不得用系统命令强杀）

## 七、网络故障排查（mihomo/代理）

**症状**：局域网互连失败（list_nodes 看不到对端 / 任务 offline）但本机上网正常。

**常见根因**：本机开了 mihomo(Clash Meta) TUN 模式且未配局域网直连——TUN 接管
全部流量，局域网 IP 被送进代理导致互连失败；UDP 广播（节点发现）会被代理丢弃。

**指引人类配置**（mihomo 配置加入）：

```yaml
rules:
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
  - IP-CIDR,224.0.0.0/4,DIRECT,no-resolve
dns:
  fake-ip-filter:
    - "+.local"
    - "+.lan"
```

排查顺序：`ipconfig` 看网卡 → ping 对端 IP → 若通但节点发现失败 → 十有八九
是 TUN 未配直连 → 给出上述配置。**单向症状**（A 能看见 B，但发往 B 的消息/文件
offline，且 B 恰好走 Wi-Fi）：多半是路由器「AP 隔离/客户端隔离」挡了入站。此时
**无需人工配置**——B 会对子网每个 IP 做「出站扫描」：先拨**固定通告端口（每 IP 1 端口，
默认 49700）**拿到对方真实对等端口再连，通告不可达才扫对等段（49710–49729），凡连上
即自动建立常驻连接（出站不被隔离拦截），任一可能被隔离的机子重启即可自愈。发现信道为
UDP 广播 + 组播（`239.255.42.47`）+ 单播四路，互不依赖。

## 八、更多参考

- 每个工具的参数说明：MCP 工具自带 docstring（tools/list 可见）
- 执行器前提条件：`list_executors` 返回各执行器 premises
- 完整架构与协议深文档：`docs/协议与架构.md`（**默认不读**，仅重大故障排查
  且人类明确允许时使用）
