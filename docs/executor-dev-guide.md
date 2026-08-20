# Executor Development Guide

> For developers who want to build executor plugins for agent-node.
> An **executor** is a backend that a node can invoke to "run one AI task and
> produce a result file". The official adapters (WorkBuddy / CodeBuddy CLI / TUI)
> are all written against this same interface — use them as reference implementations.

Companion reads: [executors/base.py](../executors/base.py) (the interface itself),
[executors/registry.py](../executors/registry.py) (loading & state machine).

## 1. What an executor is

Inside an agent-node instance, an executor is the **worker that actually runs an
AI task**: an external agent (via MCP / CLI / panel) submits a task to the node; the
node hands it to an executor; the executor does the work (e.g. drive an AI client,
run a CLI) and writes the final answer into a **result file**.

A single node can host many executors (e.g. WorkBuddy and CodeBuddy side by side).
Each executor has an **agent_id** (plugin id) the node uses for routing, work-dir
isolation, and capability broadcast.

## 2. Interface overview (spec 2.2.15 — binding contract)

Plugins are written in **Python** and subclass `ExecutorPlugin` from
`executors/base.py`. The contract splits into **binding (MUST)** vs **free (impl)**:

- **Binding**: class attrs `plugin_id / display_name / executor_type / concurrency`;
  the two abstract method signatures `check_capability / submit`; and the result-file
  contract semantics.
- **Free**: however you implement the internals (driving a GUI, spawning a process,
  polling) is entirely up to you.

### 2.1 Class attributes

| Attr | Type | Meaning |
|---|---|---|
| `plugin_id` | str (required) | Unique id, used as agent_id, e.g. `"my-exec"` |
| `display_name` | str | Human-readable name shown in the panel |
| `executor_type` | str | `interactive_tui` / `non_interactive_cli` / `gui` |
| `concurrency` | int | Concurrency cap (see section 4) |

### 2.2 Lifecycle hooks

| Method | When |
|---|---|
| `__init__(ctx)` | Registry instantiates; `ctx` is a [PluginContext](#31-plugincontext) |
| `on_load()` | Once after load & capability check (optional, default no-op) |
| `on_unload()` | On unload / rescan (optional, default no-op) |
| `check_capability()` | **Must implement**, startup self-check |
| `submit(task)` | **Must implement**, runs on task receipt |
| `is_done(task_id)` | Default: result file exists (usually no need to override) |
| `get_result(task_id)` | Default: read the result file |
| `status()` | Default reports idle/busy; override for finer detail |

### 2.3 Data classes (plain Python objects, all from base.py)

#### 3.1 PluginContext
```python
PluginContext(node_id, agent_id, work_dir, config)
# work_dir: path to this plugin's private working dir (result files / attachments)
# config:   plugin config dict (incl. auth-dialog matchers, etc.)
```

#### 3.2 CapabilityResult (return of check_capability)
```python
CapabilityResult(available: bool, reason: str | None = None, premises: list[str] | None = None)
# available=False → node does not broadcast this capability; reason records why.
# premises: disclosed prerequisites (e.g. "requires X installed").
```

#### 3.3 TaskInput (arg to submit)
```python
TaskInput(task_id: str, prompt: str, attachments: list[str],
          result_file: Path, timeout: float)
# prompt:      the full prompt (already includes "write the final answer to the result file")
# attachments: local paths under your work_dir (pushed with the task, 2.13.1)
# result_file: absolute Path where you MUST write the answer
# timeout:     seconds
```

#### 3.4 SubmitResult (return of submit)
```python
SubmitResult(ok: bool, error: ErrorCode | None = None, detail: str | None = None)
# ok=True means "task accepted / started", NOT "finished".
# error: one of ErrorCode: offline/disabled/not_installed/busy/suspended/timeout/agent_error
```

#### 3.5 ResultPayload / ExecutorStatus
```python
ResultPayload(ok, error, content)      # content of the result file
ExecutorStatus(available, state, inflight, concurrency, current_task, queue_len, until)
# state: plugin reports idle/busy only; suspended is layered on by the node core
```

## 3. The result-file contract (core concept, 2.2.11)

**All executor results are reclaimed via a result file.** The convention:

1. `submit()` receives a task and goes all-out to run `task.prompt`.
2. On completion, write the final answer to `task.result_file` (atomic: write `.tmp`,
   then rename).
3. The node decides "done" via: `is_done` → result file exists and its size is stable
   across 2 consecutive polls (registry watches it).
4. On success, `get_result` reads that file and returns its content to the caller.

> Why a file instead of a direct return? GUI / interactive executors (e.g. WorkBuddy)
> "inject into someone else's window" — they cannot synchronously get a return value.
> A file is the most robust cross-process, cross-wait handoff.

## 4. Three executor types & concurrency semantics

| executor_type | Typical impl | Node-side concurrency |
|---|---|---|
| `non_interactive_cli` | non-interactive CLI (e.g. codebuddy-cli `-p`) | **concurrent N** (=concurrency); busy-rejects, no queue |
| `interactive_tui` | resident interactive terminal (e.g. codebuddy-tui) | **1**, serialized queue (reuses resident session) |
| `gui` | desktop GUI automation (e.g. WorkBuddy UIA) | **1**, serialized queue |

- Non-interactive CLI runs a fresh process per task, so it can be concurrent (default 3).
- TUI / GUI reuse one window/session, so they must serialize — the node queues for you.

## 5. Making the node recognize your plugin (4 ways)

1. **Built-in adapter**: put your class in `executors/adapters/`, then import it in
   `load_plugins()` of `registry.py` and call `_add_plugin(MyPlugin, "<agent_id>", {})`.
   Best when shipping with the project.
2. **External plugin (recommended for user customization)**: drop a `.py` file into the
   local `data/plugins/`; restart the node (or rescan after `plugin_sync`) to auto-load.
   Requirements: it's an `ExecutorPlugin` subclass, has `plugin_id`, and its module is
   **not** inside the `executors`/`node` packages. `agent_id` = the `agent_id` attr if
   set, else `plugin_id`.
3. **Config CLI entry**: add `{agent_id, command}` to the `cli_executors` list in the
   plugin config; the node wraps it with `CliExecutorPlugin` (great for wrapping any
   CLI).
4. **mock**: a test stub; `enable_mock` in `node_config.json` defaults to true.

> Zero-touch user plugins: **no source changes needed** — just drop a `.py` into
> `%LOCALAPPDATA%\agent-node\data\plugins\` and it loads.

## 6. Minimal plugin example

Save the following as `data/plugins/hello.py` and restart the node — you get a `hello` executor.

```python
"""Example external plugin: echo the prompt back into the result file."""
from __future__ import annotations
from pathlib import Path
from executors.base import (
    CapabilityResult, ExecutorPlugin, SubmitResult, TaskInput)


class HelloPlugin(ExecutorPlugin):
    plugin_id = "hello"
    display_name = "Hello"
    executor_type = "non_interactive_cli"
    concurrency = 3

    def check_capability(self) -> CapabilityResult:
        # Always available; disclose premises to the caller
        return CapabilityResult(True, premises=["no external dependency"])

    def submit(self, task: TaskInput) -> SubmitResult:
        # NOTE: this example is synchronous/blocking; real ones spawn a thread/process
        try:
            # Write the answer to the result file (atomic write is recommended)
            tmp = task.result_file.with_suffix(".md.tmp")
            tmp.write_text(f"Got task {task.task_id}\n\n{task.prompt}",
                           encoding="utf-8")
            tmp.replace(task.result_file)
            return SubmitResult(ok=True)
        except Exception as e:
            return SubmitResult(ok=False, error="agent_error", detail=str(e))
```

> This example writes the file synchronously, so `is_done` uses the default (existence).
> Real executors (GUI/CLI injection) are usually async: `submit` returns `ok=True` right
> away; a background thread does the slow work and writes the file.

## 7. Best practices & pitfalls

- **Async first**: spawn a thread/process in `submit` and return `ok=True` quickly, so
  you don't block the node's event loop.
- **Atomic result-file write**: write `.tmp`, then `rename`, so the node never reads a
  half-written file.
- **Honest capability check**: `check_capability` probes "can it work right now"
  (is the CLI on PATH, is the GUI running); otherwise `available=False` with a `reason`
  — don't broadcast fake capabilities.
- **Attachments**: `task.attachments` are already under your `work_dir`; just read them.
- **GUI injection (2.2.14)**: for auth/login dialogs, use the matchers in `ctx.config`
  to skip them; when output indicates a login state, give a clear `auth_required` hint —
  don't treat "session expired" as a task failure.
- **Don't exceed concurrency**: for `non_interactive_cli`, the registry enforces the
  limit with a semaphore — no need to add your own lock, but keep `submit` fast-returning.
- **Use error codes correctly**: `busy` = full & no queue; `suspended` = human suspended
  it; `timeout` = ran past deadline; `agent_error` = runtime fault. Don't mix them up —
  the panel/MCP hints by code.

## 8. Debugging

- Node log (`data/node.log`) records plugin load success/fail and capability results.
- The panel's "Executors" view shows each executor's available / state / concurrency / premises.
- `data/executor_work/<agent_id>/<task_id>/` is this task's work dir: result file &
  attachments live there.

## 9. External-plugin protocol (plugin_sync distribution, 2.2.10)

Plugins can be distributed between nodes via `plugin_sync` (governed by the `allow_file`
switch; they land in the target's `data/plugins/`). The target node's `rescan` picks up
new ones. Note: `interactive_tui` / `gui` plugins require a process restart on rescan to
take effect.