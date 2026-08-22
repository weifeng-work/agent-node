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

### Option 1: one-line install (recommended, Windows)

```powershell
irm https://raw.githubusercontent.com/weifeng-work/agent-node/main/scripts/install.ps1 | iex
```

No Node / npm / git required. The script detects system Python (≥3.10, installs via winget
if missing) → downloads the source zip → creates a local `venv` (does not pollute the system)
→ installs the global `agent-node` command (pure PowerShell, added to user PATH) → creates a
desktop shortcut → auto-starts the node and opens the panel. Reinstalls/updates keep your
`data\` (identity & config).

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
| `agent-node start` | Start node |
| `agent-node stop` | Stop node |
| `agent-node status` | Show status |
| `agent-node restart` | Restart node (stays resident) |
| `agent-node update` | Update node code & dependencies |
| `agent-node help` | Help |
| `agent-node --help` | Full cli.py subcommand list |
| `agent-node <subcommand>` | AI capabilities (info/list/executors/task/...) |

## Let AI use this node

Unified access through the `agent-node` global command. Any AI that can execute shell commands can use all capabilities.

### Method A: MCP (deprecated, no longer recommended)

> MCP access is deprecated and no longer maintained. The mcp/server.py source is preserved but not updated.
> Use Method B (CLI commands) instead — fully equivalent functionality with zero client configuration.

### Method B: CLI (only access method, recommended)

```bash
agent-node register                    # register identity (once only)
agent-node list                        # list online nodes
agent-node info                        # local node overview
agent-node task --target <node_id> --executor workbuddy --prompt "a task for WorkBuddy"
agent-node mailbox                     # fetch async receipts
agent-node --help                      # full subcommand list
```

### Have any AI drive this node

Send this to any AI agent; it will read the manual, install itself, and start:

> First read the agent-node skill manual, then tell me:
> which executors are available locally? How do I call remote agents?
> Where to get the manual (any option):
> · Read it online: https://raw.githubusercontent.com/weifeng-work/agent-node/main/skill/SKILL.md
> · Already installed (Windows one-click): `%LOCALAPPDATA%\agent-node\app\skill\SKILL.md`
> · Source code: `skill\SKILL.md` in the repo root

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
│ Web Panel (FastAPI) + CLI (agent-node global command)   │
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

- **Python** ≥ 3.10 (for the node itself)
- **Windows** 10/11 (code includes Linux paths, untested on Linux)

## License

MIT. Bundled Syncthing (MPL-2.0) and psmux/tmux are distributed as standalone
executables (not derivative works); licenses are listed in [VERSIONS.md](VERSIONS.md).