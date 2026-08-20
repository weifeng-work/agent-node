# 依赖版本锁定 / Dependency Version Lock

> 本文件是构建的一级真相来源。任何版本变更请在此登记后，
> 同步修改 `requirements.txt` / `npm-dist` 及 README。
> This file is the source of truth for the build. Any version change
> must be logged here and reflected in `requirements.txt` / `npm-dist` & README.

## Python 依赖（requirements.txt，~= 锁定）

`~=` = 向上限兼容已实测大版本，自动接受补丁版；ts实测版本列于备注。

| 包 | 锁定 | 实测 | 用途 | 备注 |
|---|---|---|---|---|
| fastapi | `~=0.141` | 0.141.1 | Web 面板后端 | 传 starlette/pydantic |
| uvicorn | `~=0.52` | 0.52.4 | 面板 ASGI 服务器 | |
| starlette | `~=1.6` | 1.6.0 | fastapi 传递依赖 | 显式锁定防破坏性大版本 |
| pydantic | `~=2.13` | 2.13.4 | fastapi 数据校验 | 基线 |
| pywinauto | `~=0.6.9` (win32) | 0.6.9 | WorkBuddy UIA 注入 | 缺失时节点仍可运行 |
| comtypes | `~=1.4` (win32) | 1.4.16 | pywinauto 传递依赖 | COM 线程亲和关键 |
| websockets | `~=15.0` (win32) | 15.0.1 | TraeWork CN CDP 注入 | 缺失时节点仍可运行 |

> 安装：`python -m venv venv && venv\Scripts\python -m pip install -r requirements.txt`
> 校验：`venv\Scripts\python -m pip list`

## 预编译二进制（packaged binaries）

随 npm/dist 分发。`psyttmux` 版本经 `--version` 核验。

| 二进制 | 大小 | 版本 | 来源 | 存放 | 是否入 Git | 用途 |
|---|---|---|---|---|---|---|
| syncthing.exe | 25.7 MB | v1.29.4 "Gold Grasshopper" (go1.24.1 win-amd64) | syncthing.net 官方发布 | `bin/` + `npm-dist/app/data_synth/syncthing/` | ✅ | 文件夹同步引擎（MPL-2.0） |
| psmux.exe | 6.8 MB | tmux 3.3.8 | 项目维护者发布 | `bin/` + `npm-dist/app/bin/` | ⚠️ (计划) | 多窗格 TUI / 可见弹窗 |
| pmux.exe | 6.8 MB | tmux 3.3.8 | 同上 | 同上 | ⚠️ (计划) | psmux 别名 |
| tmux.exe | 6.8 MB | tmux 3.3.8 | 原生 tmux win32 构建 | 同上 | ⚠️ (计划) | 终端多路复用器 |

> 许可证说明：
> - Syncthing 为 **MPL-2.0**，以独立子进程 + REST 控制方式集成，非衍生作品（见 `sync/syncthing.py` 头注）。
> - psmux/pmux/tmux 以独立可执行文件分发（协议为 tmux 兼容）。
> - 本项目自身代码为 MIT（见 LICENSE）。

## 为什么要锁定

- `requirements.txt` 若用 `>=`，用户安装时会拉到**未经本项目实测**的最新版，
  升级可能带来不兼容变更（l如 fastapi 大版本 API 变动、uvicorn 配置项废弃）。
- `~=` 在"锁定已验证大版本"与"自动吃安全补丁"间取得平衡。
- 二进制随包分发即天然锁定版本；但必须登记入 VERSIONS.md 供构建溯源与审计。