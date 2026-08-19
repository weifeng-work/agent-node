"""节点主入口。

用法:
    python -m node.main [--data-dir data] [--panel-port 5177] [--peer-tcp-port 0]
                        [--no-sync] [--headless]

- 单实例保护（2.11.3）：已运行 → 拒绝并报错
- 面板绑定 127.0.0.1（2.1.5），实际 URL 写 data/panel.url
"""
from __future__ import annotations

import argparse
import signal
import sys
import threading
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="agent-node 去中心化节点")
    parser.add_argument("--data-dir", default="data")
    parser.add_argument("--panel-port", type=int, default=None)
    parser.add_argument("--peer-tcp-port", type=int, default=None)
    parser.add_argument("--no-sync", action="store_true")
    args = parser.parse_args()

    data_dir = Path(args.data_dir).resolve()
    from node.config import resolve_data_dir
    data_dir = resolve_data_dir(data_dir)

    from node.core import NodeCore
    core = NodeCore(data_dir, panel_port=args.panel_port)
    if args.peer_tcp_port is not None:
        core.config.peer_tcp_port = args.peer_tcp_port
    if args.no_sync:
        core.config.sync_enabled = False

    try:
        core.start()
    except RuntimeError as e:
        print(f"[agent-node] {e}", file=sys.stderr)
        return 1

    stop_event = threading.Event()

    def _stop(*_):
        stop_event.set()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    # 面板线程（阻塞）
    panel_thread = threading.Thread(
        target=_serve_panel, args=(core, args.panel_port), daemon=True,
        name="panel")
    panel_thread.start()

    print(f"[agent-node] 节点已启动: {core.node_id} "
          f"面板 {core.panel_url()}", flush=True)
    try:
        while not stop_event.is_set():
            stop_event.wait(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        core.stop()
    return 0


def _serve_panel(core, preferred):
    from server.panel import serve
    try:
        serve(core, preferred)
    except Exception as e:
        core.log("error", f"面板启动失败: {e}")


if __name__ == "__main__":
    sys.exit(main())
