# agent-node 去 MCP 化：迁移到「纯 Skill + 脚本集合」方案

> 状态：**已实施**（2026-08-22）
> 范围：把 AI 接入面从「MCP Server + cli.py 双通道」收敛为「Skill + cli.py 单通道」，退役 MCP。
> 核心事实：**MCP 层与 cli.py 都是面板 REST（127.0.0.1 `/api/*`）的客户端**，节点核心、P2P、执行器不依赖 MCP 协议。因此本方案不触碰核心，只是把接入面收成一个。

---

## 1. 背景与动机

### 1.1 为什么考虑放弃 MCP

1. **接入摩擦**：MCP 要求用户先在每个客户端里手工粘贴 JSON 配置（command/args/env），路径写错即失败。
2. **上下文不省反增**：MCP 工具面每次 `tools/list` 都要携带全部 schema/description；调用还包一层 JSON-RPC 信封。对"几十个工具、每个都是薄包装"的场景，这是纯开销。
3. **协议成本**：需维护 JSON-RPC stdio 会话、UTF-8 管道、`initialize/ping/tools/list/tools/call` 机制；caller_id 要"免配置"还得靠读父进程名这种 hack（见 `mcp/server.py` `_parent_process_name`）。
4. **目标体验**：用户想要的 onboarding 是"给智能体一句提示词 → 自取手册 → 安装 → 引导看面板"。MCP 需要先配置才能用，天然不满足；skill 加载即用，把**文件夹路径或下载地址**丢给智能体即可。

### 1.2 澄清：MCP 是趋势，但不是本项目的甜点

MCP 是"标准化的工具发现与调用协议"，解决的是**异构客户端 × 异构工具**的互操作问题——它的大势所趋成立在开放生态里：很多客户端、很多第三方工具、动态发现。

agent-node 的接入面是**你自己控制的一端**：一个本地 REST 后端 + 你完全掌握的工具清单。MCP 的标准化红利在这里基本不产生，反而要付上述三笔成本。**skill 的灵活性更高**：无特殊协议、无客户端配置、上下文大小由你定（SKILL.md 你控制篇幅）、且天然支持"给路径/URL 就能用"。

**诚实说明唯一的真实损失**：只支持 MCP、不允许智能体执行 shell 的客户端将无法接入。凡能跑命令的智能体（CLI 形态 Agent）完全不受影响。

---

## 2. 现状盘点

### 2.1 架构事实

```
AI 客户端
 ├─ MCP 接入   mcp/server.py  ─┐   thin forwarder
 └─ CLI 接入   tools/cli.py   ─┤   thin forwarder
                               ▼
                    面板 REST  /api/*  (127.0.0.1)
                               ▼
                    节点核心 / 执行器 / P2P / Syncthing
```

**迁移后（目标架构）**：

```
AI 客户端（能执行命令即可）
 │
 ▼
 agent-node <子命令>        ← 全局入口（agent-node.ps1 透传）
 │
 ▼
 tools/cli.py                   ← 唯一 AI 能力面（薄转发）
 │
 ▼
 面板 REST  /api/*  (127.0.0.1) ← 唯一事实后端
 │
 ▼
 节点核心 / 执行器 / P2P / Syncthing   ← 核心，不改

（mcp/server.py 保留但废弃，不维护）
```

变化只在虚线以上（接入层），虚线以下（后端 + 核心）冻结不动。

### 2.2 能力覆盖矩阵（已实施完成：MCP 能力面 → cli.py 全量对齐）

> 本节为**实施后现状**。迁移前 cli.py 仅 16 个子命令、存在 21 项缺口；实施后已全部补齐
> （见 §4 步骤 1 的分组全表），`mcp/server.py` 退役。下表仅列**新增补齐项**及其与 REST 端点的对应，作为能力对等凭证。

自 MCP 工具 ↔ cli.py 子命令映射（含原有等价项）：
`list_nodes≈list`、`send_text≈send`、`submit_task≈task`、`get_task_result≈check`、`check_mail≈mailbox/inbox`、`file_push≈upload`、`file_pull≈download`、`file_push_dir≈push-dir`、`list_dir≈ls`、`shell_exec≈shell`、`add/remove/list_anchors≈anchor`、`sync_now≈sync`、`list_executors≈executors`。

**实施中新增补齐的子命令**（能力与 MCP 对应工具等价，共 22 项）及对应 REST 端点：

| 新增子命令 | REST 端点 | 对应 MCP 工具 | 说明 |
| `info` | GET `/api/overview` | 本机概览 | |
| `peer add <host> <port>` | POST `/api/peers/add_manual` | add_manual_peer | |
| `peer remove <host>` | POST `/api/peers/remove_manual` | remove_manual_peer | |
| `conversations` | GET `/api/chat/conversations` | list_conversations | |
| `history --peer <node_id> [--limit]` | GET `/api/chat/history` | get_chat_history | |
| `plugins` | GET `/api/plugins` | list_plugins | |
| `plugin push --node <id> --path <file>` | POST `/api/plugins/distribute` | distribute_plugin | |
| `mailbox-all [--limit]` | GET `/api/mailbox/all` | mailbox_all | |
| `mailbox-clean --mode consumed\|expired [--before]` | POST `/api/mailbox/cleanup` | cleanup_mailbox | |
| `executor status <id>` | GET `/api/executors/status` | get_executor_status | |
| `executor suspend <id> [--on\|--off] [--reason] [--until]` | POST `/api/executors/suspend` | set_executor_suspend | |
| `executor restart <id>` | POST `/api/executors/restart` | restart_plugin | |
| `executor sessions [<id>]` | GET `/api/executors/sessions` | list_agent_sessions | |
| `comm-log [--peer] [--direction] [--type] [--corr] [--limit]` | GET `/api/logs/comm` | get_comm_log | |
| `node-log [--source] [--lines] [--level]` | GET `/api/logs/node` | get_node_log | |
| `config` | GET `/api/settings` | get_config | |
| `rename <name>` | POST `/api/settings/name` | rename_node | |
| `executor rename <id> <name>` | POST `/api/executors/rename` | rename_executor | |
| `team <team_id>` | POST `/api/settings/team` | set_team | |
| `switch <allow_shell\|allow_file\|allow_ai_task> <on\|off>` | POST `/api/settings/switch` | set_control_state | |
| `admin <on\|off>` | POST `/api/settings/admin` | set_run_as_admin | |

`get_skill` 无 cli.py 等价项，属**有意的能力面收敛**而非遗漏：纯 skill 模式下 SKILL.md 即智能体加载入口，深文档由智能体直接读文件即可，不再需要单独的取手册工具。能力面由 MCP 38 工具收敛为 cli.py 全量子命令（含命令参数组合），语义等价。

### 2.3 安装 / 全局命令现状与 AI 调用路径

**一键安装产出的东西**（`scripts/install.ps1`）：
- `%LOCALAPPDATA%\agent-node\app\`：代码（含 `tools/cli.py`）
- `%LOCALAPPDATA%\agent-node\venv\`：Python 虚拟环境 + 依赖
- `%LOCALAPPDATA%\agent-node\data\`：运行时数据
- `%LOCALAPPDATA%\agent-node\agent-node.ps1 / .cmd`：全局 CLI 命令，安装时加入**用户 PATH**

**关键事实：全局 `agent-node` 命令≠AI 能力入口。**
查看 `scripts/agent-node.ps1` 的 `switch ($Action)`（第 140-147 行），它只支持
`start / stop / status / restart / update / mcp`——是一个**节点生命周期壳**，**并不透传
`tools/cli.py` 的能力面**（任务/文件/聊天/日志/设置等）。而 `tools/cli.py` 目前必须以
`python tools/cli.py ...` 在 `app/` 目录下运行（仓库形态）。

**AI 调用 cli.py 的真实路径**有两种，迁移时必须选择并写进 SKILL.md：
1. **全局透传（推荐）**：给 `agent-node.ps1` 的 `switch` 加 `default` 分支，**未匹配命令自动透传**到
   `& $venvPy "$APP\tools\cli.py" $Action @args`（如
   `agent-node task --target ... --executor ...`）。**装完即有全局 AI 能力入口**，skill 只引导装配并调用这一个命令。
2. **源码目录调用**：SKILL.md 引导 AI `cd %LOCALAPPDATA%\agent-node\app` 后跑
   `python tools/cli.py ...`（或调 venv python）。缺点：每次 cd、路径易错，对 AI 不友好。

**对本方案的意义**：若想让"AI 用 skill 引导 irm 装到全局 → 之后 AI 调用全局 agent-node 命令"闭环，迁移必须包含**全局透传**这一步——否则 AI 拿到的"全局命令"没有任务/文件等能力。参见第 4 节步骤 1B。

### 2.4 安全边界（已验证）

对本仓库做了依赖扫描（`grep import mcp|from mcp|mcp.server`）：

- **核心零依赖**：`node/`、`transport/`、`executors/`、`server/`、`sync/` 均无任何 mcp 引用。
- `mcp` 仅被**非核心**对象引用：`mcp/server.py` 自身、`README*.md`、`skill/SKILL.md`、`scripts/agent-node.ps1`（打印 MCP 配置）、`tests/test_mcp.py`。

**安全性结论**：
- 运行时改动 = **纯增量**：只向 `tools/cli.py` 添加子命令，且每个子命令只是调用**已存在、已被 MCP 反复验证过**的 REST 端点（`/api/*`），不新增协议、不改核心行为。
- 其余改动全部是**文档/测试层面**（SKILL.md 改版、README 文案、test_mcp.py 标记 skip），不影响节点运行。
- 回滚安全：核心未动，若迁移后发现问题，保留 mcp/ 或还原文档即可，无架构回滚成本。

**唯一非架构的行为差异**：AI 客户端身份（caller_id）来源从"MCP 自动派生（父进程名）"变为"register/环境变量配置"。这是身份获取方式的变化，符合既有约束"CLI 智能体从配置文件读取 caller_id"，不影响协议与数据面。

---

## 3. 取舍分析

| 维度 | 保留 MCP | 纯 Skill + cli.py | 结论 |
|---|---|---|---|
| 用户接入 | 需粘贴客户端 JSON | 一句提示词 / 给路径 | skill 胜 |
| 上下文开销 | tools/list + JSON-RPC 信封 | 只加载一份 SKILL.md | skill 胜 |
| 调用可靠性 | 结构化调用 | shell 拼接 + default fallback 路由 | MCP 胜，需规范对冲 |
| 能力发现 | 动态 schema | 静态文档 | skill 够用（清单固定） |
| 维护成本 | 双文档双测试双协议 | 单通道单文档 | skill 胜 |
| 客户端覆盖 | MCP-only 客户端可接 | 需可执行 shell | MCP 胜（唯一真损失） |
| 与核心耦合 | 无（薄转发） | 无（薄转发） | 平 |

**取舍结论**：若目标用户是"能跑命令的智能体"（Trae / Claude Code / CodeBuddy CLI 等 CLI 形态 Agent），纯 skill 方案全面更优；唯一不可覆盖的是纯 MCP-only 客户端。**采纳方案 B：skill + cli.py 为唯一接入面，mcp/ 源码保留但标注"已废弃，不维护"**——留着无坏处，也不产生维护负担，只是 SKILL.md/README 不再引导免配置。

---

## 4. 迁移步骤

### 步骤 0 · 冻结后端（不改核心）
面板 REST `/api/*` 保持为**唯一事实后端**，任何 AI 能力都只通过它。本方案不改 `node/`、`transport/`、`executors/`、`server/` 核心。

### 步骤 1 · cli.py 补齐对等能力面
按第 2.2 节清单，为 `tools/cli.py` 增加 21 个子命令。全部复用现有 `call()`/`out()` 模式（自动注入 `X-Caller-Id`、统一 JSON 输出），无新协议。执行器相关可归并为一个 `executor` 子命令树（status/suspend/restart/rename/sessions）。

**补齐后的完整子命令分组表**（现有 + 新增 = 全量，AI 通过 `agent-node <子命令>` 调用）：

| 分组 | 子命令 | 说明 | 状态 |
|---|---|---|---|
| **本机** | `info` | 本机节点概览（node_id/名称/team/端口/面板/三开关/在线） | 新增 |
| | `config` | 读本机配置 | 新增 |
| | `rename <name>` | 节点改名（仅本机） | 新增 |
| | `team <team_id>` | 设置/切换 team（危险操作） | 新增 |
| | `switch <allow_shell\|allow_file\|allow_ai_task> <on\|off>` | 三开关 | 新增 |
| | `admin <on\|off>` | 管理员权限（需重启生效） | 新增 |
| **节点** | `list` | 在线节点列表 | 已有 |
| | `peer add <host> <port>` | 手动加对端 | 新增 |
| | `peer remove <host>` | 手动删对端 | 新增 |
| | `anchor list\|add\|remove` | 锚点管理（出站回连自愈） | 已有 |
| **聊天** | `send --to <node_id> --text` | 发文本消息 | 已有 |
| | `conversations` | 会话列表 | 新增 |
| | `history --peer <node_id> [--limit]` | 会话历史 | 新增 |
| **文件** | `upload --to <node_id> --file <path> [--target]` | 推送文件 | 已有 |
| | `download --from <node_id> --path <path>` | 拉取文件 | 已有 |
| | `push-dir --root <dir> [--to] [--target]` | 整目录推送 | 已有 |
| | `ls --node <node_id> --path <path> [--recursive]` | 列目录 | 已有 |
| | `sync` | 触发同步目录扫描 | 已有 |
| **执行器** | `executors` | 执行器列表（本机+远程） | 已有 |
| | `executor status <id>` | 执行器深态 | 新增 |
| | `executor suspend <id> --on\|--off [--reason] [--until]` | 挂起/恢复 | 新增 |
| | `executor restart <id>` | 重启插件 | 新增 |
| | `executor rename <id> <name>` | 执行器改名 | 新增 |
| | `executor sessions [<id>]` | 交互式会话列表 | 新增 |
| | `task --target <node_id> --executor <id> --prompt "..." [--mode] [--timeout] [--attach] [--task-id]` | 统一调用执行器 | 已有 |
| | `check --task <task_id>` | 查询任务结果 | 已有 |
| **邮箱** | `mailbox` | 取异步邮箱回执（自己的） | 已有 |
| | `mailbox-all [--limit]` | 邮箱全量（监控视角） | 新增 |
| | `mailbox-clean --mode consumed\|expired [--before]` | 清理邮箱 | 新增 |
| **日志** | `comm-log [--peer] [--direction] [--type] [--corr] [--limit]` | 通信日志 | 新增 |
| | `node-log [--source] [--lines] [--level]` | 节点运行日志 | 新增 |
| **插件** | `plugins` | 列出磁盘插件 | 新增 |
| | `plugin push --node <id> --path <file>` | 分发插件 | 新增 |
| **命令** | `shell --to <node_id> --cmd "command" [--timeout]` | 远程命令 | 已有 |
| | `diag` | 一键健康自检 | 已有 |
| | `register` | 生成/读取 caller_id 身份 | 已有 |

### 步骤 1B · 全局命令透传（default fallback 透传未知命令到 cli.py）
不再使用 `cli` 子命令方案，改为在 `agent-node.ps1` 的 `switch` 中加 `default` 分支：
**switch 中任何未匹配的命令自动透传给 cli.py**。这样 AI 永远只记 `agent-node <子命令>` 一个模式。

**改法 1·param 去掉 ValidateSet 限制**（第 8 行，改为无约束的 `$Action`）：
```powershell
# 改前
[ValidateSet("start","stop","status","restart","update","mcp","help")]
param($Action)

# 改后：去掉 ValidateSet，让任何未知命令都能进入 default 分支
param($Action)
```

**改法 2·switch 末尾加 default 透传分支**：
```powershell
default {
    # 非生命周期命令 → 透传给 cli.py
    $venvPy = Join-Path $ROOT "venv\Scripts\python.exe"
    if (-not (Test-Path $venvPy)) {
        Write-Host ("未安装或缺少 venv: " + $venvPy) -ForegroundColor Red
        exit 1
    }
    & $venvPy "$APP\tools\cli.py" $Action @args
    exit $LASTEXITCODE
}
```

**改法 3·help 输出**（default 分支前保留，或直接去掉，让 default 透传到 cli.py `--help`）：
```powershell
"help" { Write-Host "用法: agent-node start|stop|status|restart|update|<子命令>" }
# 或直接去掉 help 分支：agent-node help → 透传 cli.py --help（更丰富）
```

**路由逻辑**（AI 视角完全透明）：
```bash
agent-node start        → switch 匹配 "start" → 生命周期
agent-node status       → switch 匹配 "status" → 生命周期
agent-node restart      → switch 匹配 "restart" → 生命周期（kill + 重启）
agent-node info         → 无匹配 → default → cli.py info
agent-node task --target xxx --executor yyy --prompt "..."  → 无匹配 → default → cli.py task ...
agent-node help         → 保留为 lifecycle 或透传 cli.py --help
```

### 步骤 2 · SKILL.md 重写为独立接入手册
改写 `skill/SKILL.md`，去掉 MCP 配置章节，结构草案：
1. **接入即用**：给智能体的第一段话（能执行命令、能访问文件系统即可；本机节点安装路径或下载地址）。
2. **安装与引导**：教 AI 判断两条路径——
   - A·AI 代装（一句话提示词闭环）：AI 有 shell 权限，直接在 PowerShell 跑 `irm https://raw.githubusercontent.com/weifeng-work/agent-node/main/scripts/install.ps1 | iex`，然后刷新当前会话 PATH（`$env:Path += ";$env:LOCALAPPDATA\agent-node"`），`agent-node status` 确认在线后引导用户开面板（`agent-node info` 拿面板地址/端口）。
   - B·人装 + AI 引导：用户（或 AI 指导用户）先跑 irm 装好，AI 再接管"看面板 / 改配置 / 日常协作"。
   - 之后日常全部走**全局命令**：`agent-node <子命令>`（见步骤 1B）。
3. **身份**：先 `agent-node register` 生成 caller_id，或设置环境变量 `AGENT_NODE_CALLER_ID`；说明"caller_id = 你的专属异步邮箱键"（沿用现有约束）。
4. **子命令全表**：按 本机/节点/聊天/文件/执行器/日志/设置 分组，每行一句。
5. **三模式 + 幂等 + 错误码 + 行为边界**：直接沿用现有 SKILL.md 第五、六节内容。
6. **传参规范（Windows）**：中文/JSON 参数用双引号包裹；路径含空格加引号；优先传 JSON 文件的场景说明；GBK/UTF-8 注意事项。
7. **故障排查**：沿用现有第七节（mihomo/TUN、AP 隔离）。
8. **深文档索引**：`docs/` 按需读。

### 步骤 3 · 身份机制
- 保留 `agent-node register`（写 `~/.config/agent-node/caller.json`）。
- 保留环境变量 `AGENT_NODE_CALLER_ID`（优先级高于配置文件，与现有 `caller_id()` 一致）。
- skill 手册里明确：**同一客户端稳定用同一身份文件，不要每次临时生成**。

### 步骤 4 · MCP 标记废弃（保留源码，不维护）
本方案采用**方案 B**：mcp/ 目录源码保留，不做任何代码删改，但 SKILL.md/README 不再引导 MCP 配置，`agent-node.ps1` 的 `mcp` 子命令保留但标记废弃。

具体操作：
- `skill/SKILL.md`：删除整个"方式 A: MCP"章节，不再提及 MCP 配置。
- `README.md`：MCP 章节折叠或下移，加注"已废弃，推荐使用 `agent-node`"。
- `scripts/agent-node.ps1`：`Show-McpConfig` 函数保留，`switch` 中 `"mcp"` 分支保留，但 help 输出从 `start|stop|status|restart|update|mcp|help` 改为 `start|stop|status|restart|update|<子命令>`（mcp 降级为隐藏命令，不展示在 help 中；未知命令由 default fallback 透传 cli.py）。
- `tests/test_mcp.py`：保留不删，但不再随 CI 运行（标记 `skip` 或移入独立目录 `tests/legacy/`）。

### 步骤 5 · 测试迁移
- 新建 `tests/test_cli.py`，覆盖 cli.py 全部子命令，断言 JSON 输出结构、错误码映射、参数校验。
- `tests/test_mcp.py` 保留不动，标记 `pytest.mark.skip` 不再随 CI 运行（见步骤 4）。
- 保留 `tests/test_protocol.py`、`test_core.py` 不动（核心无关）。

### 步骤 6 · 文档/版本/发布同步
- README 改为"skill 优先"接入说明（含给智能体的一句话提示词示例）；`irm | iex` 一键安装文案**原样保留**（安装机制不受影响，见 2.3）。
- 本方案落地伴随版本号更新（按现有单源流程：commit → push → tag → release）。
- CHANGELOG 记录"去 MCP 化 + 全局 agent-node 透传"。

---

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| Windows shell 引号/编码导致调用失败 | SKILL.md 固化传参规范与示例；cli.py 保持 JSON 输出、参数由 argparse 校验 |
| 能力面漂移（文档与实现不一致） | 单一 cli.py 为唯一事实源；SKILL.md 只列命令名，参数细节以 `agent-node <子命令> --help` 为准 |
| 纯 MCP-only 客户端失联 | 明确受众为可执行命令的智能体；若需要可保留 MCP 可选入口（方案 B） |
| 智能体误改节点源码/配置 | 沿用现有行为边界约束写入 SKILL.md（文档约束，不加技术防护） |
| 异步回执丢失 | 复用现有 SQLite inbox 持久化 + `consumed` 恢复机制，与接入方式无关 |
| `caller_id` 混乱导致回执串线 | 配置文件机制 + skill 明确"同一客户端固定身份"，不做每次临时生成 |

---

## 6. 验收清单

- [x] `cli.py` 21 项缺口全部补齐，`cli.py --help` 分组清晰、每项与 REST 端点一一对应
- [x] `agent-node <子命令>` 全局透传可用，未知命令自动 fallback 到 cli.py
- [x] 三模式（sync/async/trigger）+ 幂等 `task_id` 经 agent-node 验证通过
- [x] caller_id 经 register / 环境变量两种途径注入正常，异步回执只进本人邮箱
- [x] SKILL.md 不依赖 MCP 即可独立完成"AI 跑 irm 安装 → 引导看面板 → `agent-node` 日常协作"
- [x] `mcp/` 源码保留，SKILL.md/README 不再引导 MCP 配置，`agent-node` 默认 fallback 到 cli.py（方案 B）
- [ ] `irm | iex` 安装链路回归通过（装完 `$env:Path` 刷新后 `agent-node status`、`agent-node info`、面板可开）
- [x] 核心测试 `test_protocol/test_core/test_executor` 全绿，新增 agent-node 层测试通过
- [x] 版本号按单源流程更新，README 接入说明同步

---

## 7. 附录 A：给智能体的一句话提示词模板

以下是用户给智能体的提示词示例（可放在系统提示、项目说明、或对话首条消息中）：

```
你可以通过 agent-node 节点协作框架与本机及局域网其他电脑上的智能体协作。

接入步骤：
1. 如果本机还没装 agent-node，在 PowerShell 跑：
   irm https://raw.githubusercontent.com/weifeng-work/agent-node/main/scripts/install.ps1 | iex
   装完会自动启动节点。接下来刷新当前会话 PATH 让 agent-node 立即可用：
   $env:Path += ";$env:LOCALAPPDATA\agent-node"

2. 跑 agent-node status 确认节点在线，拿到面板地址。

3. 首次使用先注册身份（只需一次）：
   agent-node register

4. 之后所有操作都用 agent-node <子命令>：
   - agent-node info          — 本机概览
   - agent-node list          — 在线节点
   - agent-node executors     — 可用执行器
   - agent-node task --target <node_id> --executor <agent_id> --prompt "任务指令" --mode async
   - agent-node mailbox       — 取异步回执

完整子命令表见 agent-node --help，或读 skill/SKILL.md。
```

**设计要点**：
- 不需要任何客户端配置（对比 MCP 需要粘贴 JSON）
- 不需要记路径（对比 `cd %LOCALAPPDATA%\agent-node\app && python tools/cli.py`）
- 智能体看到这段话就能自取手册、自行安装、自行调用

---

## 8. 附录 B：SKILL.md 完整重写草案

以下是迁移后 `skill/SKILL.md` 的完整内容草案，供实施时直接替换用。

```markdown
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
```

**与现有 SKILL.md 的差异摘要**：
- 删除整个"方式 A: MCP"章节及 JSON 配置示例
- "方式 B: CLI"升级为唯一接入方式，全局命令 `agent-node` 取代 `python tools/cli.py`
- 子命令表从 16 项扩展到 37 项（全量覆盖 MCP 能力面）
- 新增"传参规范（Windows）"章节
- 身份章节从"MCP 自动派生"改为"register 一次 + 环境变量"
- 错误码、行为边界、故障排查内容直接沿用（不变）
