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

## 二、两种接入方式（能力等价）

### 方式 A: MCP（推荐，支持 MCP 的智能体）

MCP 客户端 JSON 配置：

```json
{
  "mcpServers": {
    "agent-node": {
      "command": "python",
      "args": ["-m", "mcp.server"],
      "cwd": "C:/Users/YOUR_USERNAME/AppData/Local/agent-node/app",
      "env": {
        "AGENT_NODE_PANEL": "http://127.0.0.1:5177"
      }
    }
  }
}
```

> `cwd` 填你机器上节点的 **app 安装目录**（默认 `%LOCALAPPDATA%\agent-node\app`；运行
> `agent-node` 命令，其 MCP 引导会打印你机器上的真实路径）。`AGENT_NODE_PANEL` 的
> `5177` 是**默认**面板端口，若被占用节点会自动改用 5178…，实际地址以
> `agent-node status` 或启动日志显示为准。

**caller_id 是你的身份**（MCP server 进程身份，随每次工具调用自动注入，
你无需记住它）。**caller_id 对应你的专属收件箱**——异步任务回执只进你的邮箱，
其他 AI 客户端看不到。caller_id 由系统在 MCP server 启动时读取**父进程可执行名**
（如 workbuddy/trae）自动派生：同一客户端重启后 id 不变、不同客户端 id 不同、
本机不冲突，**无需配置**。

### 方式 B: CLI（不支持 MCP 但有 Bash 能力的智能体）

```bash
cd %LOCALAPPDATA%\agent-node\app   # 到节点安装目录（默认；以你机器实际为准）
python tools/cli.py register          # 首次: 生成 caller_id 身份文件
python tools/cli.py list              # 已知节点列表
python tools/cli.py task --target <node_id> --executor <agent_id> \
    --prompt "任务指令" --mode async   # 发任务（--mode sync|async|trigger）
python tools/cli.py inbox             # 取异步回执（caller_id 自动注入）
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
3b. mode=async  → 立即返回，继续干别的；回执进你的邮箱，之后 check_inbox() 取
3c. mode=trigger → 只要"触发成功"确认，不要结果
```

**幂等重试**：调用失败重发时传同一 `task_id`，目标节点不会重复执行。

### 跨机文件

```
file_push(node_id, local_path, target_path?)   # 推送（默认落对方 data/inbox/）
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

**禁止**：
- **修改任何节点源码 / 打补丁 / 改配置文件**（擅自改动会让该节点行为与其他
  节点不一致，破坏协同）
- **擅自启动 / 停止 / 重启节点进程**（节点生命周期由人类掌控；异常时给人类
  重启指引，报告可用 get_node_log 辅助诊断）
- 直接构造协议报文绕过语义工具（一切远程操作走 MCP 工具/cli.py）

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
是 TUN 未配直连 → 给出上述配置。

## 八、更多参考

- 每个工具的参数说明：MCP 工具自带 docstring（tools/list 可见）
- 执行器前提条件：`list_executors` 返回各执行器 premises
- 完整架构与协议深文档：`docs/协议与架构.md`（**默认不读**，仅重大故障排查
  且人类明确允许时使用）
