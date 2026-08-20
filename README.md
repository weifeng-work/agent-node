# agent-node

去中心化局域网多智能体协作节点 —— AI 可控的增强版 LocalSend。

[English](./README.en.md) | [中文](./README.md)

## 这是什么

`agent-node` 让局域网内的一台台电脑互相成为彼此的能力。每台电脑运行一个节点，
节点间**自动发现**、**点对点直连**（无中心服务器、无账号、纯局域网明文）。任何接入
节点的 AI 智能体可以获得：

1. **调用其他电脑上的 AI 智能体** —— 执行器（CodeBuddy CLI/TUI、WorkBuddy，可扩展插件）
2. **跨机文件传输** —— 推送/拉取/浏览任意目录，带文件选择器与收件历史
3. **跨机聊天与远程命令** —— LocalSend 式聊天 + 受开关管辖的远程 shell

> 设计原则：最小拓扑、全放开、明文、纯点对点。不设中心、不依赖公网。

## 快速开始

### 方式一：npm 安装（推荐，Windows）

```bash
npm install -g @weifeng-work/agent-node
agent-node
```

首次运行会自动完成安装（检测 Python → 创建虚拟环境 → 安装依赖 → 配置防火墙），
约 1-3 分钟。安装完成后启动节点，面板自动打开，并输出 MCP 配置与 AI 引导词。

> 若 npm 提示 `allow-scripts` 警告（新版 npm 默认拦截安装脚本），直接运行
> `agent-node` 即可——首次运行会自动检测并补装。

### 方式二：源码运行

```bash
# 前置: Python ≥ 3.10, Windows 10/11
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python -m node.main --data-dir data
```

## 命令行

| 命令 | 说明 |
|---|---|
| `agent-node` | 启动节点（已运行则打开面板） |
| `agent-node stop` | 停止节点 |
| `agent-node status` | 查看状态 |
| `agent-node setup` | 安装或修复 |
| `agent-node uninstall` | 完整卸载（含数据目录） |
| `agent-node help` | 帮助 |

## 让 AI 使用本节点

两种接入方式能力等价：

### 方式 A：MCP（推荐）

把以下 JSON 加入你的 AI 客户端（如 Claude Desktop）配置：

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

> `AGENT_NODE_PANEL` 的 `5177` 是**默认**面板端口；若被占用节点会自动改用 5178…，
> 实际地址以 `agent-node status` 或启动日志显示为准。

caller_id（异步回执专属邮箱的身份键）**由系统自动派生**：MCP server 启动时读取父进程
可执行名（如 workbuddy/trae），无需配置，同一台电脑上不同客户端互不冲突。内置 30+ 工具。

### 方式 B：CLI

```bash
python -m tools.cli register        # 生成身份
python -m tools.cli list            # 列出节点与执行器
python -m tools.cli task --target <node_id> --executor workbuddy --prompt "要 WorkBuddy 做的任务"
python -m tools.cli push-dir --root <目录> --to <node_id> --target inbox/migrated   # 整目录树推送（免打包）
```

### 让任何 AI 自动驾驶本节点

把下面这句话发给任意智能体，它会读取节点内置说明书后指导你：

> 请加载 skill/SKILL.md 说明书，学会使用 agent-node 节点后，告诉我：
> 本机有哪些可用执行器？怎么调用远程智能体？

## MCP 之后如何使用更多功能

- **自动发现**：局域网内所有 agent-node 实例自动互见，无需配置
- **调用远程智能体**：跨机提交任务（sync 同步 / async 异步 / trigger 触发）
- **文件推送/拉取**：节点选择 + 文件/文件夹选择器 + 历史
- **文件夹同步**：Syncthing 驱动，auto 局域网配对，面板看每节点同步完成度
- **聊天 / 远程 shell**：LocalSend 式聊天 + 受开关管辖的 shell

## 架构

```
每节点（自用局域网，明文，全放开）：
┌──────────────────────────────────────────────┐
│ 节点核心 NodeCore（UDP beacon + TCP mesh）   │
│ 执行器注册表（CodeBuddy/WorkBuddy/...插件）  │
│ 文件控制 · 聊天 · shell · Syncthing 托管     │
│ Web 面板（FastAPI）+ MCP Server + CLI        │
└──────────────────────────────────────────────┘
```

无中心、无代理、纯点对点。详细设计与协议见 [docs/重构设计方案.md](docs/重构设计方案.md)。

## 文档

| 文档 | 说明 |
|---|---|
| [skill/SKILL.md](skill/SKILL.md) | AI 智能体使用节点的官方说明书（不需要双语，大模型通晓主流语言） |
| [docs/重构设计方案.md](docs/重构设计方案.md) | 施工方案、需求树、附录 A 线级协议 |
| [docs/协议与架构.md](docs/协议与架构.md) | 深文档（门控，线级协议与架构完整规范） |
| [docs/执行器开发指南.md](docs/执行器开发指南.md) | 开发者编写执行器插件的接口与功能实现指南 |
| [docs/executor-dev-guide.md](docs/executor-dev-guide.md) | Executor development guide (English) |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更记录 |
| [VERSIONS.md](VERSIONS.md) | 依赖与二进制版本锁定清单 |

## 前提条件

- **Node.js** ≥ 14（npm 启动器用）
- **Python** ≥ 3.10（节点本体）
- **Windows** 10/11（代码含 Linux 路径，未在 Linux 实测）

## 许可证

MIT。随包分发的 Syncthing（MPL-2.0）与 psmux/tmux 以独立可执行文件分发，
不构成衍生作品，许可证见 [VERSIONS.md](VERSIONS.md)。