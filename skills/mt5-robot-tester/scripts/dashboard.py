#!/usr/bin/env python3
"""Local control panel (HTML dashboard) for the mt5-robot-tester pipeline.

A tiny stdlib HTTP server (bound to 127.0.0.1 only) that:

  * lists the bots in each folder (candidates / in-testing / finalists),
  * shows each bot's current phase and verdict from ``state.json``,
  * lets you launch / stop the pipeline with a button,
  * streams the tail of ``run.log``.

A plain HTML file cannot read your folders or start MetaTrader 5 (browsers
sandbox that), so this small local server does it and the page talks to it.

Usage:
    python3 dashboard.py --config C:/path/config.json --output-dir C:/path/out
    # then open http://127.0.0.1:8765/  (opens automatically)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
_HTML = _HERE.parent / "assets" / "dashboard.html"
_PIPELINE = _HERE / "mt5_batch_tester.py"


# --- pure, testable status helpers ---------------------------------------

def derive_bot_view(name: str, st: dict) -> dict:
    """Reduce a state.json bot entry to a compact dashboard row."""
    status = st.get("status")
    if status == "done":
        phase = "done"
    elif "round3" in st:
        phase = "R3"
    elif "round2" in st:
        phase = "R2"
    elif "round1" in st:
        phase = "R1"
    else:
        phase = "pending"
    verdict = st.get("verdict") if status == "done" else None
    r2 = st.get("round2") or {}
    final = (st.get("round3") or {}).get("final_metrics") or {}
    gate = st.get("round1") or {}
    return {
        "name": name,
        "phase": phase,
        "verdict": verdict,
        "passed": verdict == "finalist",
        "reason": st.get("reason") or gate.get("reason"),
        "best_symbol": gate.get("best_symbol"),
        "r2_profit": r2.get("net_profit"),
        "final_profit": final.get("net_profit"),
        "final_dd_pct": final.get("drawdown_max_pct"),
        "lr": r2.get("lr_correlation"),
    }


def _list_ex5(folder: Optional[Path]) -> list[str]:
    if not folder or not folder.exists():
        return []
    return sorted(p.stem for p in folder.glob("*.ex5"))


def build_status(config: dict, output_dir: Path, running: bool) -> dict:
    folders = config.get("folders", {})

    def _p(key):
        return Path(folders[key]) if folders.get(key) else None

    cand, intest, fin = _p("candidates"), _p("in_testing"), _p("finalists")

    state_path = output_dir / "state.json"
    state = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            state = {}
    bots = [derive_bot_view(n, st) for n, st in state.get("bots", {}).items()]
    bots.sort(key=lambda b: (b["phase"] != "done", b["name"].lower()))

    log_tail = []
    log_path = output_dir / "run.log"
    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
            log_tail = lines[-40:]
        except OSError:
            pass

    finalists = sum(1 for b in bots if b["passed"])
    rejected = sum(1 for b in bots if b["verdict"] in ("rejected", "error"))
    return {
        "running": running,
        "folders": {
            "candidates": _list_ex5(cand),
            "in_testing": _list_ex5(intest),
            "finalists": _list_ex5(fin),
        },
        "bots": bots,
        "summary": {
            "tested": len(bots),
            "finalists": finalists,
            "rejected": rejected,
            "candidates_left": len(_list_ex5(cand)),
        },
        "log_tail": log_tail,
    }


# --- server ---------------------------------------------------------------

class Dashboard:
    def __init__(self, config_path: Path, output_dir: Path):
        self.config_path = config_path
        self.output_dir = output_dir
        self.config = json.loads(config_path.read_text(encoding="utf-8"))
        self.proc: Optional[subprocess.Popen] = None
        self.lock = threading.Lock()

    def is_running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> dict:
        with self.lock:
            if self.is_running():
                return {"ok": False, "message": "Ya está en ejecución"}
            cmd = [sys.executable, str(_PIPELINE),
                   "--config", str(self.config_path),
                   "--output-dir", str(self.output_dir), "--resume"]
            self.proc = subprocess.Popen(cmd)
            return {"ok": True, "message": "Pipeline lanzado", "pid": self.proc.pid}

    def stop(self) -> dict:
        with self.lock:
            if not self.is_running():
                return {"ok": False, "message": "No está en ejecución"}
            self.proc.terminate()
            return {"ok": True, "message": "Deteniéndose…"}

    def status(self) -> dict:
        # Reload config each poll so folder edits are reflected.
        try:
            self.config = json.loads(self.config_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
        return build_status(self.config, self.output_dir, self.is_running())


def make_handler(dash: Dashboard):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes, ctype: str):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, code=200):
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")

        def do_GET(self):  # noqa: N802
            if self.path in ("/", "/index.html"):
                try:
                    html = _HTML.read_text(encoding="utf-8")
                except OSError:
                    html = "<h1>dashboard.html no encontrado</h1>"
                self._send(200, html.encode("utf-8"), "text/html; charset=utf-8")
            elif self.path.startswith("/api/status"):
                self._json(dash.status())
            else:
                self._send(404, b"not found", "text/plain")

        def do_POST(self):  # noqa: N802
            if self.path.startswith("/api/start"):
                self._json(dash.start())
            elif self.path.startswith("/api/stop"):
                self._json(dash.stop())
            else:
                self._send(404, b"not found", "text/plain")

        def log_message(self, *args):  # silence default logging
            pass

    return Handler


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Panel de control local del pipeline MT5.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    if not config_path.exists():
        print(f"error: config no encontrada: {config_path}", file=sys.stderr)
        return 1

    dash = Dashboard(config_path, output_dir)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), make_handler(dash))
    url = f"http://127.0.0.1:{args.port}/"
    print(f"Panel de control en {url}  (Ctrl+C para salir)")
    if not args.no_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nCerrando…")
        server.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
