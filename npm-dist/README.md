# agent-node

Decentralized LAN multi-agent collaboration node — AI-controllable enhanced LocalSend.

去中心化局域网多智能体协作节点。

## Quick Start / 快速开始

```bash
npm install -g @weifeng-work/agent-node
agent-node
```

> If your npm shows an `allow-scripts` warning during install (newer npm blocks
> postinstall scripts by default), just run `agent-node` anyway — it detects a
> missing install and completes setup automatically on first run.
>
> 如果安装时 npm 提示 `allow-scripts` 警告（新版 npm 默认拦截安装脚本），
> 直接运行 `agent-node` 即可——首次运行会自动检测并完成安装。

## What it does / 它能做什么

- **Auto-discover** all agent-node instances on your LAN (no config needed)
- **Call remote AI agents** (CodeBuddy CLI/TUI, WorkBuddy, extensible plugins) from any node
- **Transfer files** between machines (push/pull/browse, with file picker)
- **Sync folders** across all nodes (Syncthing-powered, auto-pairing)
- **Chat** between nodes (LocalSend-style)
- **Remote shell** execution
- **Web panel** at `http://127.0.0.1:5177` for human oversight
- **MCP Server** built-in — your AI client gets 30+ tools

## Architecture / 架构

```
Every node (LAN, plaintext, fully open):
┌──────────────────────────────────────────────┐
│ Node Core (UDP beacon + TCP mesh + tasks)    │
│ Executor Registry (CodeBuddy/WorkBuddy/...)  │
│ File control + Chat + Shell + Syncthing      │
│ Web Panel (FastAPI) + MCP Server + CLI       │
└──────────────────────────────────────────────┘
```

No hub. No broker. Pure peer-to-peer.

## Commands / 命令

| Command | Description |
|---------|-------------|
| `agent-node` | Start node (or open panel if already running) |
| `agent-node stop` | Stop node |
| `agent-node status` | Show status |
| `agent-node setup` | Install or repair |
| `agent-node uninstall` | Full uninstall |

## MCP Integration / MCP 接入

Add to your AI client (e.g. Claude Desktop) `claude_desktop_config.json`:

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

> `AGENT_NODE_PANEL` 的 `5177` 是默认面板端口；若被占用节点会自动改用 5178…，实际地址以 `agent-node status` 为准。

caller_id（异步回执专属邮箱的身份键）由系统自动派生（父进程可执行名），无需配置。

## Prerequisites / 前提条件

- **Node.js** ≥ 14 (for the npm launcher)
- **Python** ≥ 3.10 (for the node itself)
- **Windows** 10/11 (Linux support in code, untested)

## Docs / 文档

- [SKILL.md](./app/skill/SKILL.md) — AI agent manual
- [协议与架构.md](./app/docs/协议与架构.md) — Deep architecture doc (gated)

## License

MIT
