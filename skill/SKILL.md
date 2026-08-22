# agent-node 节点协作 SKILL 说明书

> 本文件是智能体（AI）使用 agent-node 节点能力的**唯一官方说明书**。
> 加载本 skill 即学会与本机节点及全网节点协作。

## 一、这是什么

agent-node 是局域网多机智能体协作节点。每台电脑跑一个节点，节点间自动发现、
点对点直连（无中心服务器）。你（AI）通过**本机节点**获得三样能力：

1. **调用其他电脑上的智能体**（执行器：CodeBuddy CLI/TUI、WorkBuddy 等）
2. **跨机文件传输**（推送/拉取/浏览任意目录）
3. **跨机聊天与远程命令**

> **安装**（Windows）：PowerShell 一行
> `irm https://raw.githubusercontent.com/weifeng-work/agent-node/main/scripts/install.ps1 | iex`
> ——自动下源码 + venv 装依赖 + 建桌面快捷方式 + 启动节点。
> 日常控制用 `agent-node start|stop|status|restart|update`，
> AI 能力调用用 `agent-node <子命令>`。

## 二、接入方式（唯一入口）

**前置条件**：你能执行 shell 命令（PowerShell / Bash）。

### 安装

```powershell
irm https://raw.githubusercontent.com/weifeng-work/agent-node/main/scripts/install.ps1 | iex
```

装完自动启动节点。刷新当前会话 PATH 让 `agent-node` 立即可用：
```powershell
$env:Path += ";$env:LOCALAPPDATA\agent-node"
```

确认在线：
```bash
agent-node status
```

### 身份注册（首次，只需一次）

```bash
agent-node register
```
生成 `~/.config/agent-node/caller.json`，内含你的 `caller_id`。
**caller_id = 你的专属异步邮箱键**——异步任务回执只进你的邮箱，其他 AI 看不到。
同一客户端稳定用同一身份文件，不要每次临时生成。

也可用环境变量 `AGENT_NODE_CALLER_ID` 覆盖（优先级高于配置文件）。

### 日常调用

所有操作统一走全局命令：
```bash
agent-node <子命令> [参数]
```

## 三、核心概念（防混淆）

| 标识 | 含义 |
|---|---|
| `node_id` | 节点唯一身份（`node-<主机名>-<短码>`），**寻址一律用它** |
| `name` | 节点显示名（人改的，可重复，仅展示） |
| `agent_id` | 执行器条目在本节点的标识（如 `codebuddy-cli`/`workbuddy`） |
| `executor_id` | **全局寻址** = `<node_id>/<agent_id>`，AI 用它调执行器 |
| `caller_id` | **你的**身份（收件箱归属键，register 生成） |

## 四、子命令全表

### 本机
| 命令 | 说明 |
|---|---|
| `agent-node info` | 本机节点概览（node_id/名称/team/端口/面板/三开关/在线） |
| `agent-node config` | 读本机配置 |
| `agent-node rename <name>` | 节点改名（仅本机） |
| `agent-node team <team_id>` | 设置/切换 team（危险操作：断开旧 team 连接） |
| `agent-node switch <allow_shell\|allow_file\|allow_ai_task> <on\|off>` | 三开关 |
| `agent-node admin <on\|off>` | 管理员权限（需重启生效） |

> 重启节点使用生命周期命令：`agent-node restart`（保持驻留，面板短暂中断后自动恢复）

### 节点
| 命令 | 说明 |
|---|---|
| `agent-node list` | 在线节点列表 |
| `agent-node peer add <host> <port>` | 手动加对端（跨网段兜底） |
| `agent-node peer remove <host>` | 手动删对端 |
| `agent-node anchor list\|add\|remove` | 锚点管理（被 AP 隔离时出站回连自愈） |

### 聊天
| 命令 | 说明 |
|---|---|
| `agent-node send --to <node_id> --text "消息"` | 发文本消息 |
| `agent-node conversations` | 会话列表 |
| `agent-node history --peer <node_id> [--limit 200]` | 会话历史 |

### 文件
| 命令 | 说明 |
|---|---|
| `agent-node upload --to <node_id> --file <路径> [--target <目标路径>]` | 推送文件 |
| `agent-node download --from <node_id> --path <远程路径>` | 拉取文件（落本机收件目录） |
| `agent-node push-dir --root <目录> [--to <node_id>] [--target <目标基础路径>]` | 整目录推送 |
| `agent-node ls --node <node_id> --path <路径> [--recursive]` | 列目录 |
| `agent-node sync` | 触发同步目录扫描 |

### 执行器
| 命令 | 说明 |
|---|---|
| `agent-node executors` | 本机+远程执行器统一列表 |
| `agent-node executor status <id>` | 执行器深态 |
| `agent-node executor suspend <id> --on\|--off [--reason] [--until]` | 挂起/恢复 |
| `agent-node executor restart <id>` | 重启插件 |
| `agent-node executor rename <id> <name>` | 执行器改名 |
| `agent-node executor sessions [<id>]` | 交互式会话列表 |
| `agent-node task --target <node_id> --executor <id> --prompt "..." [--mode sync\|async\|trigger] [--timeout 600] [--attach f1,f2] [--task-id <幂等键>]` | 调用执行器 |
| `agent-node check --task <task_id>` | 查询任务结果 |

### 邮箱
| 命令 | 说明 |
|---|---|
| `agent-node mailbox` | 取异步邮箱回执（自己的） |
| `agent-node mailbox-all [--limit 300]` | 邮箱全量（监控视角） |
| `agent-node mailbox-clean --mode consumed\|expired [--before <时间>]` | 清理邮箱 |

### 日志
| 命令 | 说明 |
|---|---|
| `agent-node comm-log [--peer] [--direction] [--type] [--corr] [--limit 200]` | 通信日志 |
| `agent-node node-log [--source node\|executor\|syncthing] [--lines 200] [--level]` | 节点运行日志 |

### 插件
| 命令 | 说明 |
|---|---|
| `agent-node plugins` | 列出磁盘插件 |
| `agent-node plugin push --node <node_id> --path <文件>` | 分发插件 |

### 其他
| 命令 | 说明 |
|---|---|
| `agent-node shell --to <node_id> --cmd "command" [--timeout 60]` | 远程命令（受目标 allow_shell） |
| `agent-node node-update --node <node_id> [--timeout 300]` | 远程更新目标节点的代码 |
| `agent-node diag` | 一键健康自检 |
| `agent-node register` | 生成/读取 caller_id 身份（首次） |

## 五、常用工作流

### 调用远程智能体

**调用方向规则（铁律）：**
- **主智能体**：人类当前绑定的智能体（GUI/对话中那个），是**唯一有"发起调用"权**的一方。
- **CLI 智能体**：只作为**被调用方**（后台/远方的 `cli.py` 注册身份），**只被调用，不自行调用**其他智能体。
- 因此调用方向**单向无环**（主智能体 → 其他智能体），杜绝递归/循环调用；发起方身份恒等于"主智能体/人类当前执行器"，路由与回执因此稳定。

```
1. agent-node executors       → 拿到所有可用执行器
2. agent-node task --target <node_id> --executor <agent_id> --prompt "任务指令" --mode async
3a. --mode sync   → 阻塞等结果，一次拿到（简单任务）
3b. --mode async  → 立即返回，回执进你的邮箱，之后 agent-node mailbox 取
3c. --mode trigger → 只要"触发成功"确认，不要结果
```

**三模式中文对照**：

| 中文说法 | mode 值 | 行为 |
|---|---|---|
| 同步调用 | `sync` | 阻塞等结果，一次拿到 |
| 异步调用 | `async` | 立即返回，结果回执进你的异步邮箱，稍后 `mailbox` 取 |
| 仅触达 | `trigger` | 只要"触发成功"确认，不要结果 |

**使用建议**：简单快任务用 sync；长任务用 async；通知类用 trigger。
异步回执可能很久才到——发出后继续干别的，回执到达后 `mailbox` 取即可。

**幂等重试**：调用失败重发时传同一 `--task-id`，目标节点不会重复执行。

### 跨机文件

```
agent-node upload --to <node_id> --file <本地路径>              # 推送（默认落对方 data/inbox/）
agent-node push-dir --root <目录> --to <node_id>                # 整目录推送
agent-node download --from <node_id> --path <远程路径>           # 拉取（落本机收件目录）
agent-node ls --node <node_id> --path <路径>                     # 浏览对方目录
```

## 六、传参规范（Windows）

- 含空格的路径用双引号包裹：`--file "C:\Users\My Documents\report.txt"`
- 中文文本参数用双引号：`--text "你好世界"`
- 逗号分隔的附件不加空格：`--attach file1.txt,file2.txt`
- JSON 输出统一 UTF-8；若终端显示乱码，检查 `chcp`（推荐 `chcp 65001`）

## 七、错误码

| 错误码 | 含义 | 你的应对 |
|---|---|---|
| `offline` | 目标节点不在线 | 稍后重试或告知人类 |
| `disabled` | 目标节点对应开关关闭 | 告知人类去目标机开开关 |
| `not_installed` | 执行器不可用 | 换执行器或告知人类 |
| `busy` | 非交互执行器满并发 | 稍后重试 |
| `suspended` | 执行器被人类挂起 | 告知人类，别硬试 |
| `timeout` | 超时 | 可带同一 task-id 重试 |
| `agent_error` | 智能体本身报错 | 读 detail 判断 |

## 八、行为边界（必须遵守）

**允许**：
- 经 `agent-node` 操作节点与本网其他节点
- 读取节点运行状况与日志（`agent-node node-log` / `agent-node comm-log`）
- 经 `agent-node plugin push` 分发插件、`agent-node executor restart` 重启插件
- 经 `agent-node restart` 重启本机节点进程（保持驻留）

**禁止**：
- **修改任何节点源码 / 打补丁 / 改配置文件**
- **关闭 / 卸载 / 停止驻留节点进程**（仅允许保持驻留的重启）

## 九、网络故障排查（mihomo/代理）

**症状**：局域网互连失败（list 看不到对端 / 任务 offline）但本机上网正常。

**常见根因**：mihomo(Clash Meta) TUN 模式未配局域网直连。

**指引人类配置**（mihomo 配置加入）：
```yaml
rules:
  - IP-CIDR,192.168.0.0/16,DIRECT,no-resolve
  - IP-CIDR,10.0.0.0/8,DIRECT,no-resolve
  - IP-CIDR,172.16.0.0/12,DIRECT,no-resolve
```

**AP 隔离**（单向症状：A 能看见 B 但发往 B 失败，B 走 Wi-Fi）：
路由器"客户端隔离"挡了入站。B 会对子网做出站扫描自动建立常驻连接，
重启即可自愈。无需人工配置。

## 十、更多参考

- 每个子命令的参数细节：`agent-node <子命令> --help`
- 执行器前提条件：`agent-node executors` 返回各执行器 premises
- 完整架构与协议深文档：`docs/协议与架构.md`（默认不读，仅重大故障排查时使用）