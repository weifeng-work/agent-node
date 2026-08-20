# agent-node

A decentralized LAN multi-agent collaboration node — AI-controllable enhanced LocalSend.

[English](./README.en.md) | [中文](./README.md)

## What it is

`agent-node` turns each machine on your LAN into an extension of the others. Every computer
runs one node; nodes **auto-discover**, and connect **peer-to-peer** (no central server, no
account, LAN-plaintext only). Any AI agent connected to a node can:

1. **Call AI agents on other machines** — executors (CodeBuddy CLI/TUI, WorkBuddy, extensible plugins)
2. **Transfer files across machines** — push/pull/browse any directory, with a file/folder picker and history
3. **Chat & run remote commands** — LocalSend-style chat + switch-gated remote shell

> Design principles: minimal topology, fully open, plaintext, pure peer-to-peer.
> No hub, no account, no public network dependency.

## Quick start

### Option 1: npm install (recommended, Windows)

```bash
npm install -g @weifeng-work/agent-node
agent-node
```

First run auto-installs (detect Python → create venv → install deps → configure firewall),
~1-3 min. Once installed, the web panel opens automatically, and the MCP JSON config plus
an AI onboarding prompt are printed to the terminal.

> If npm warns about `allow-scripts` (newer npm blocks postinstall by default), just run
> `agent-node` anyway — it detects the missing install and completes setup on first run.

### Option 2: run from source

```bash
# Prereq: Python ≥ 3.10, Windows 10/11
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python -m node.main --data-dir data
```

## CLI

| Command | Description |
|---|---|
| `agent-node` | Start node (or open panel if already running) |
| `agent-node stop` | Stop node |
| `agent-node status` | Show status |
| `agent-node setup` | Install or repair |
| `agent-node uninstall` | Full uninstall (incl. data dir) |
| `agent-node help` | Help |

## Let an AI use this node

Two equivalent access methods:

### Method A: MCP (recommended)

Add this JSON to your AI client (e.g. Claude Desktop):

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

> `5177` in `AGENT_NODE_PANEL` is the **default** panel port; if occupied the node
> automatically switches to 5178… — the actual address is shown by `agent-node status`.

`caller_id` (the identity key for your private async-receipt inbox) is derived
automatically: the MCP server reads its parent process executable name
(e.g. workbuddy/trae) at startup — no config needed, distinct clients on the
same machine never collide. 30+ tools built in.

### Method B: CLI

```bash
python -m tools.cli register        # create identity
python -m tools.cli list            # list nodes & executors
python -m tools.cli task --target <node_id> --executor workbuddy --prompt "a task for WorkBuddy"
python -m tools.cli push-dir --root <dir> --to <node_id> --target inbox/migrated   # push whole directory tree (no packing)
```

### Have any AI drive this node

Send this message to any AI agent; it will read the built-in manual and guide you:

> Please load the skill/SKILL.md manual, then tell me:
> what executors are available? How to call remote agents?

## What you can do after MCP

- **Auto-discover**: all agent-node instances on the LAN see each other, no config
- **Call remote agents**: cross-machine task submission (sync / async / trigger)
- **Push / pull files**: node selector + file/folder picker + history
- **Folder sync**: Syncthing-powered, auto pair on the same team, per-node completion in the panel
- **Chat / remote shell**: LocalSend-style chat + switch-gated shell

## Architecture

```
Each node (self-hosted LAN, plaintext, fully open):
┌──────────────────────────────────────────────┐
│ Node Core (UDP beacon + TCP mesh)            │
│ Executor Registry (CodeBuddy/WorkBuddy/...)  │
│ File control · Chat · Shell · Syncthing      │
│ Web Panel (FastAPI) + MCP Server + CLI       │
└──────────────────────────────────────────────┘
```

No hub. No broker. Pure peer-to-peer. Design & protocol details:
[docs/重构设计方案.md](docs/重构设计方案.md) (Chinese).

## Docs

| Doc | About |
|---|---|
| [skill/SKILL.md](skill/SKILL.md) | Official manual for AI agents (Chinese only — LLMs understand all major languages) |
| [docs/协议与架构.md](docs/协议与架构.md) | Deep-dive doc (gated; wire-level protocol & architecture) |
| [docs/executor-dev-guide.md](docs/executor-dev-guide.md) | Executor development guide (English) |
| [docs/执行器开发指南.md](docs/执行器开发指南.md) | Executor development guide (Chinese) |
| [CHANGELOG.md](CHANGELOG.md) | Changelog (Chinese) |
| [VERSIONS.md](VERSIONS.md) | Dependency & binary version lock list |

## Prerequisites

- **Node.js** ≥ 14 (for the npm launcher)
- **Python** ≥ 3.10 (for the node itself)
- **Windows** 10/11 (code includes Linux paths, untested on Linux)

## License

MIT. Bundled Syncthing (MPL-2.0) and psmux/tmux are distributed as standalone
executables (not derivative works); licenses are listed in [VERSIONS.md](VERSIONS.md).