#!/usr/bin/env python3
"""Orchestrate the 3-round MT5 bot-selection pipeline from the command line.

For every ``.ex5`` in the *candidates* folder:

  Round 1  ``Optimization=3`` over all Market-Watch symbols. Gate: >=5 symbols
           profitable AND best symbol profit >= 3x deposit.
  Round 2  single backtest on the best symbol; analyze the quality profile
           (net profit %, worst drawdown %, % positive months, all years
           positive, LR Correlation, months-to-new-high).
  Round 3  sequential per-parameter optimization of the 5-6 inputs after
           ``MagicNumber`` (range +/-50%, step 5%), one at a time; then a final
           backtest with the optimized set.
  Finalist optimized result improves on Round 2 AND profit >= 4x deposit AND
           worst drawdown <= 12%.

Tested bots move to *in-testing*; finalists are also copied to *finalists* with
their optimized ``.set``. Progress is checkpointed to ``state.json`` (``--resume``
continues where it stopped) and a cross-run learning store reorders Round-3
parameters by historical impact.

Pure helpers (INI builders, range/param/finalist logic) are unit-tested without
MT5. Launching MT5 requires Windows + the terminal at run time.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
try:
    from mt5_common import to_float
    from parse_mt5_optimization import (best_param_value, gate_round1, parse_passes)
    from parse_mt5_report import parse_report_file
    from mt5_learnings import Learnings
except ImportError:  # pragma: no cover - import shim
    sys.path.insert(0, str(_HERE))
    from mt5_common import to_float
    from parse_mt5_optimization import (best_param_value, gate_round1, parse_passes)
    from parse_mt5_report import parse_report_file
    from mt5_learnings import Learnings

DEFAULTS = {
    "common": {
        "period": "H1",
        "from": "2020.01.01",
        "to": "2026.06.30",
        "model": 4,               # every tick based on real ticks
        "deposit": 10000,
        "currency": "USD",
        "leverage": 100,
        "delay_ms": 32,
        "optimization_criterion": 0,   # Rentabilidad máxima (max balance)
    },
    "gates": {
        "round1_min_positive": 5,
        "round1_min_profit_multiple": 3.0,
        "finalist_min_profit_multiple": 4.0,
        "finalist_max_drawdown_pct": 12.0,
        "finalist_require_improvement": True,
    },
    "round2_reference": {
        "min_profit_pct": 300.0,
        "max_drawdown_pct": 15.0,
        "min_pct_positive_months": 70.0,
        "require_all_years_positive": True,
        "min_lr_correlation": 0.80,
        "max_months_to_new_high": 3,
    },
    "round3": {
        "range_pct": 0.5,
        "step_pct": 0.05,
        "params_after_magic": 6,
        "magic_param_name": "MagicNumber",
        "use_learned_order": True,
    },
    "timeout_seconds": 3600,
}


# ----------------------------------------------------------------------------
# Pure, unit-tested helpers
# ----------------------------------------------------------------------------

def normalize_date(value: str) -> str:
    return str(value).strip().replace("-", ".").replace("/", ".")


def experts_relpath(candidates_dir: str | Path) -> str:
    """Path of the candidates folder relative to ``MQL5\\Experts`` (for Expert=)."""
    parts = list(Path(candidates_dir).parts)
    for i in range(len(parts) - 1, -1, -1):
        if parts[i].lower() == "experts":
            return "\\".join(parts[i + 1:])
    # Fall back to just the folder name.
    return Path(candidates_dir).name


def fmt_num(value) -> str:
    """Format a number for an INI without scientific notation or noisy decimals."""
    f = float(value)
    if f == int(f):
        return str(int(f))
    return ("%.10g" % f)


def compute_range(value: float, range_pct: float, step_pct: float):
    """Return (start, step, stop) for +/-range_pct around ``value``, step_pct step.

    Returns ``None`` for a zero value (cannot scale). Integer-valued inputs get
    integer bounds and a step of at least 1.
    """
    if value == 0:
        return None
    lo = value * (1 - range_pct)
    hi = value * (1 + range_pct)
    start, stop = (lo, hi) if lo <= hi else (hi, lo)
    step = abs(value) * step_pct
    if float(value) == int(value):
        start, stop = int(round(start)), int(round(stop))
        step = max(1, int(round(step)))
    return (start, step, stop)


def select_strategy_params(ordered_names: list[str], magic_name: str,
                           count: int) -> list[str]:
    """The ``count`` input names immediately after ``magic_name`` (5-6 params).

    If MagicNumber is absent, fall back to the first ``count`` names.
    """
    lowered = [n.lower() for n in ordered_names]
    try:
        idx = lowered.index(magic_name.lower())
        after = ordered_names[idx + 1:]
    except ValueError:
        after = ordered_names[:]
    return after[:count]


def evaluate_finalist(final_metrics: dict, r2_profit: Optional[float],
                      deposit: float, gates: dict) -> tuple[bool, str]:
    """Finalist iff improved AND profit >= Nx deposit AND worst DD <= limit."""
    profit = final_metrics.get("net_profit")
    dd = final_metrics.get("drawdown_max_pct")
    profit_threshold = gates["finalist_min_profit_multiple"] * deposit
    dd_limit = gates["finalist_max_drawdown_pct"]

    reasons = []
    improved = True
    if gates.get("finalist_require_improvement", True):
        improved = (profit is not None and r2_profit is not None
                    and profit > r2_profit)
        if not improved:
            reasons.append(f"no mejora R2 ({profit} <= {r2_profit})")
    if profit is None or profit < profit_threshold:
        reasons.append(f"beneficio {profit} < {profit_threshold:.0f} (≥"
                       f"{gates['finalist_min_profit_multiple']}x)")
    if dd is None or dd > dd_limit:
        reasons.append(f"DD {dd}% > {dd_limit}%")

    passed = not reasons and improved
    return passed, ("ok" if passed else "; ".join(reasons))


def evaluate_round2_profile(metrics: dict, deposit: float, ref: dict) -> dict:
    """Score the Round-2 quality profile against the reference thresholds."""
    profit_pct = metrics.get("profit_pct")
    dd = metrics.get("drawdown_max_pct")
    pos_months = metrics.get("pct_positive_months")
    all_years = metrics.get("all_years_positive")
    lr = metrics.get("lr_correlation")
    mtnh = metrics.get("max_months_to_new_high")
    checks = {
        "profit_pct": profit_pct is not None and profit_pct >= ref["min_profit_pct"],
        "drawdown": dd is not None and dd < ref["max_drawdown_pct"],
        "positive_months": (pos_months is not None
                            and pos_months > ref["min_pct_positive_months"]),
        "all_years_positive": (all_years is True
                               if ref["require_all_years_positive"] else True),
        "lr_correlation": lr is not None and lr >= ref["min_lr_correlation"],
        "months_to_new_high": (mtnh is not None
                               and mtnh <= ref["max_months_to_new_high"]),
    }
    checks["all_passed"] = all(checks.values())
    return checks


def parse_set_file(path: str | Path) -> list[tuple[str, object]]:
    """Parse an MT5 ``.set`` into an ordered [(name, value)] list.

    Ignores the ``name,F`` / ``name,1..3`` optimization-range companion lines.
    """
    inputs: list[tuple[str, object]] = []
    for raw in Path(path).read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith(";") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if "," in key:      # companion optimization line
            continue
        num = to_float(value)
        inputs.append((key.strip(), num if num is not None else value.strip()))
    return inputs


def inputs_from_passes(passes: list[dict]) -> list[tuple[str, object]]:
    """Ordered [(name, value)] of EA inputs taken from an optimization report."""
    for p in passes:
        inputs = p.get("inputs")
        if inputs:
            return list(inputs.items())
    return []


# ----------------------------------------------------------------------------
# INI builders
# ----------------------------------------------------------------------------

def _base_tester_lines(expert: str, symbol: str, common: dict,
                       report_no_ext: Path) -> list[str]:
    return [
        "[Tester]",
        f"Expert={expert}",
        f"Symbol={symbol}",
        f"Period={common['period']}",
        f"Model={common['model']}",
        f"FromDate={normalize_date(common['from'])}",
        f"ToDate={normalize_date(common['to'])}",
        "ForwardMode=0",
        f"Deposit={common['deposit']}",
        f"Currency={common['currency']}",
        f"Leverage={common['leverage']}",
        f"Report={report_no_ext}",
        "ReplaceReport=1",
        "ShutdownTerminal=1",
        "Visual=0",
    ]


def build_round1_ini(expert: str, common: dict, report_no_ext: Path,
                     seed_symbol: str = "EURUSD") -> str:
    """Optimization=3: iterate every Market-Watch symbol."""
    lines = _base_tester_lines(expert, seed_symbol, common, report_no_ext)
    lines.insert(-4, "Optimization=3")
    lines.insert(-4, f"OptimizationCriterion={common['optimization_criterion']}")
    return "\n".join(lines) + "\n"


def build_backtest_ini(expert: str, symbol: str, common: dict,
                       report_no_ext: Path,
                       inputs: Optional[list[tuple[str, object]]] = None) -> str:
    """Optimization=0: single backtest, optionally with fixed [TesterInputs]."""
    lines = _base_tester_lines(expert, symbol, common, report_no_ext)
    lines.insert(-4, "Optimization=0")
    text = "\n".join(lines) + "\n"
    if inputs:
        text += "\n[TesterInputs]\n"
        for name, value in inputs:
            text += f"{name}={_fmt_input(value)}\n"
    return text


def build_optimize_param_ini(expert: str, symbol: str, common: dict,
                             report_no_ext: Path,
                             strategy_inputs: list[tuple[str, object]],
                             target: str, rng: tuple) -> str:
    """Optimization=1 over a single ``target`` parameter; others fixed."""
    lines = _base_tester_lines(expert, symbol, common, report_no_ext)
    lines.insert(-4, "Optimization=1")
    lines.insert(-4, f"OptimizationCriterion={common['optimization_criterion']}")
    text = "\n".join(lines) + "\n\n[TesterInputs]\n"
    start, step, stop = rng
    for name, value in strategy_inputs:
        if name == target:
            text += (f"{name}={_fmt_input(start)}||{_fmt_input(start)}||"
                     f"{_fmt_input(step)}||{_fmt_input(stop)}||Y\n")
        else:
            text += f"{name}={_fmt_input(value)}\n"
    return text


def _fmt_input(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return fmt_num(value)
    return str(value)


# ----------------------------------------------------------------------------
# Terminal / report I/O
# ----------------------------------------------------------------------------

def find_terminal(explicit: Optional[str] = None) -> Optional[Path]:
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    if os.environ.get("MT5_TERMINAL_PATH"):
        candidates.append(Path(os.environ["MT5_TERMINAL_PATH"]))
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        if not base:
            continue
        candidates.append(Path(base) / "MetaTrader 5" / "terminal64.exe")
        candidates.extend(Path(base).glob("*/terminal64.exe"))
    for path in candidates:
        if path and path.exists():
            return path
    return None


def _find_report(report_no_ext: Path, exts=(".xml", ".htm", ".html")) -> Optional[Path]:
    for ext in exts:
        candidate = report_no_ext.with_suffix(ext)
        if candidate.exists():
            return candidate
    return None


def run_tester(terminal: Path, ini_path: Path, report_no_ext: Path,
               timeout: int, portable: bool, exts) -> tuple[Optional[Path], str]:
    """Launch MT5 for one config and return (report_path, status)."""
    stale = _find_report(report_no_ext, exts)
    if stale:
        try:
            stale.unlink()
        except OSError:
            pass
    cmd = [str(terminal), f"/config:{ini_path}"]
    if portable:
        cmd.append("/portable")
    try:
        subprocess.run(cmd, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    except FileNotFoundError:
        return None, "terminal_not_found"
    report = _find_report(report_no_ext, exts)
    return (report, "ok") if report else (None, "no_report")


# ----------------------------------------------------------------------------
# State / log
# ----------------------------------------------------------------------------

class Pipeline:
    def __init__(self, config: dict, output_dir: Path, terminal: Optional[Path],
                 timeout: int, portable: bool, resume: bool):
        self.config = config
        self.common = {**DEFAULTS["common"], **config.get("common", {})}
        self.gates = {**DEFAULTS["gates"], **config.get("gates", {})}
        self.ref = {**DEFAULTS["round2_reference"], **config.get("round2_reference", {})}
        self.r3 = {**DEFAULTS["round3"], **config.get("round3", {})}
        self.output_dir = output_dir
        self.terminal = terminal
        self.timeout = timeout
        self.portable = portable
        self.resume = resume

        self.reports_dir = output_dir / "mt5_reports"
        self.ini_dir = output_dir / "mt5_ini"
        self.sets_dir = output_dir / "sets"
        for d in (self.reports_dir, self.ini_dir, self.sets_dir):
            d.mkdir(parents=True, exist_ok=True)

        self.state_path = output_dir / "state.json"
        self.log_path = output_dir / "run.log"
        self.state = self._load_state()

        refs_dir = _HERE.parent / "references"
        self.learn = Learnings(output_dir / "learnings.json")
        self._learn_md = refs_dir / "learnings.md"

        folders = config.get("folders", {})
        self.candidates = Path(folders["candidates"]) if folders.get("candidates") else None
        self.in_testing = Path(folders["in_testing"]) if folders.get("in_testing") else None
        self.finalists = Path(folders["finalists"]) if folders.get("finalists") else None
        self.expert_prefix = (config.get("experts_relpath")
                              or (experts_relpath(self.candidates) if self.candidates else ""))

    def _load_state(self) -> dict:
        if self.resume and self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass
        return {"schema_version": "1.0",
                "started": dt.datetime.now().isoformat(timespec="seconds"),
                "bots": {}}

    def _save_state(self) -> None:
        self.state_path.write_text(json.dumps(self.state, indent=2,
                                              ensure_ascii=False), encoding="utf-8")

    def log(self, msg: str) -> None:
        line = f"{dt.datetime.now().isoformat(timespec='seconds')}  {msg}"
        print(line, flush=True)
        with self.log_path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    # --- per-bot pipeline --------------------------------------------------

    def expert_path(self, ex5: Path) -> str:
        return f"{self.expert_prefix}\\{ex5.name}" if self.expert_prefix else ex5.name

    def _run(self, ini_text: str, name: str, report_stub: str, exts) -> tuple:
        ini_path = self.ini_dir / f"{name}__{report_stub}.ini"
        ini_path.write_text(ini_text, encoding="utf-8")
        report_no_ext = (self.reports_dir / f"{name}__{report_stub}").resolve()
        report, status = run_tester(self.terminal, ini_path, report_no_ext,
                                    self.timeout, self.portable, exts)
        return report, status, ini_path

    def process_bot(self, ex5: Path) -> dict:
        name = ex5.stem
        st = self.state["bots"].setdefault(name, {"status": "pending"})
        if self.resume and st.get("status") == "done":
            self.log(f"[{name}] ya completado ({st.get('verdict')}), se omite")
            return st

        expert = self.expert_path(ex5)

        # --- Round 1 -------------------------------------------------------
        if "round1" not in st:
            self.log(f"[{name}] Ronda 1: optimización todos los símbolos")
            ini = build_round1_ini(expert, self.common,
                                   (self.reports_dir / f"{name}__R1").resolve())
            report, status, _ = self._run(ini, name, "R1", (".xml", ".htm", ".html"))
            if status != "ok":
                return self._finish(name, "error", f"R1 {status}", st)
            passes = parse_passes(report)
            gate = gate_round1(passes, self.common["deposit"],
                               self.gates["round1_min_positive"],
                               self.gates["round1_min_profit_multiple"])
            st["round1"] = gate
            st["inputs"] = inputs_from_passes(passes)
            self._save_state()
        gate = st["round1"]
        if not gate["passed"]:
            return self._finish(name, "rejected", f"R1: {gate['reason']}", st)

        best_symbol = gate["best_symbol"]
        self.learn.record_best_pair(best_symbol, gate.get("best_profit"))

        # --- Round 2 -------------------------------------------------------
        if "round2" not in st:
            self.log(f"[{name}] Ronda 2: backtest en {best_symbol}")
            ini = build_backtest_ini(expert, best_symbol, self.common,
                                     (self.reports_dir / f"{name}__R2").resolve())
            report, status, _ = self._run(ini, name, "R2", (".htm", ".html", ".xml"))
            if status != "ok":
                return self._finish(name, "error", f"R2 {status}", st)
            metrics = parse_report_file(report)
            st["round2"] = metrics
            st["round2_profile"] = evaluate_round2_profile(
                metrics, self.common["deposit"], self.ref)
            self._save_state()
        r2 = st["round2"]
        r2_profit = r2.get("net_profit")

        # --- Round 3: sequential parameter optimization --------------------
        if "round3" not in st:
            st["round3"] = self._round3(name, expert, best_symbol, st)
            self._save_state()
        r3 = st["round3"]

        final = r3.get("final_metrics") or {}
        passed, reason = evaluate_finalist(final, r2_profit,
                                           self.common["deposit"], self.gates)
        verdict = "finalist" if passed else "rejected"
        st["final_reason"] = reason
        return self._finish(name, verdict, reason, st, ex5=ex5,
                            optimized_set=r3.get("best_set"))

    def _round3(self, name: str, expert: str, symbol: str, st: dict) -> dict:
        ordered = [(n, v) for n, v in st.get("inputs", [])]
        ordered_names = [n for n, _ in ordered]
        strat_names = select_strategy_params(
            ordered_names, self.r3["magic_param_name"], self.r3["params_after_magic"])
        if self.r3.get("use_learned_order", True):
            strat_names = self.learn.param_priority_order(strat_names)

        # Working values: start from discovered defaults.
        values = dict(ordered)
        strategy_inputs = [(n, values.get(n)) for n in
                           select_strategy_params(ordered_names,
                                                  self.r3["magic_param_name"],
                                                  self.r3["params_after_magic"])]
        best_profit = st["round2"].get("net_profit")
        per_param = []

        for target in strat_names:
            v = values.get(target)
            if not isinstance(v, (int, float)):
                continue
            rng = compute_range(v, self.r3["range_pct"], self.r3["step_pct"])
            if rng is None:
                continue
            self.log(f"[{name}] R3 optimiza {target} en {rng}")
            cur_inputs = [(n, values[n]) for n, _ in strategy_inputs]
            ini = build_optimize_param_ini(
                expert, symbol, self.common,
                (self.reports_dir / f"{name}__R3_{target}").resolve(),
                cur_inputs, target, rng)
            report, status, _ = self._run(ini, name, f"R3_{target}",
                                          (".xml", ".htm", ".html"))
            if status != "ok":
                per_param.append({"param": target, "status": status})
                continue
            passes = parse_passes(report)
            best_val = best_param_value(passes, target, by="profit")
            top = max((p for p in passes if isinstance(p.get("profit"), (int, float))),
                      key=lambda p: p["profit"], default=None)
            new_profit = top.get("profit") if top else None
            improvement = ((new_profit or 0) - (best_profit or 0)) if new_profit is not None else 0
            if best_val is not None and new_profit is not None and new_profit >= (best_profit or float("-inf")):
                values[target] = best_val
                best_profit = new_profit
            self.learn.record_param_optimization(target, improvement)
            per_param.append({"param": target, "best_value": best_val,
                              "profit": new_profit, "improvement": improvement})

        best_set = [(n, values.get(n)) for n in ordered_names]
        strat_set = [(n, values.get(n)) for n in
                     select_strategy_params(ordered_names,
                                            self.r3["magic_param_name"],
                                            self.r3["params_after_magic"])]

        # Final backtest with the optimized strategy inputs.
        final_metrics = {}
        self.log(f"[{name}] R3 backtest final optimizado en {symbol}")
        ini = build_backtest_ini(expert, symbol, self.common,
                                 (self.reports_dir / f"{name}__final").resolve(),
                                 inputs=strat_set)
        report, status, _ = self._run(ini, name, "final", (".htm", ".html", ".xml"))
        if status == "ok":
            final_metrics = parse_report_file(report)
        else:
            final_metrics = {"status": status}

        return {"per_param": per_param, "best_set": best_set,
                "strategy_set": strat_set, "final_metrics": final_metrics}

    def _finish(self, name: str, verdict: str, reason: str, st: dict,
                ex5: Optional[Path] = None,
                optimized_set: Optional[list] = None) -> dict:
        st["status"] = "done"
        st["verdict"] = verdict
        st["reason"] = reason
        self.learn.record_bot(name, verdict, reason)
        self.log(f"[{name}] => {verdict.upper()} ({reason})")
        if ex5 is not None and verdict != "error":
            self._move_bot(ex5, verdict, optimized_set)
            st["moved"] = True
        self._save_state()
        return st

    def _move_bot(self, ex5: Path, verdict: str, optimized_set: Optional[list]) -> None:
        # Copy finalists (with optimized .set) before moving the .ex5.
        if verdict == "finalist" and self.finalists:
            self.finalists.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(ex5, self.finalists / ex5.name)
                if optimized_set:
                    self._write_set(self.finalists / f"{ex5.stem}.set", optimized_set)
            except OSError as e:
                self.log(f"[{ex5.stem}] aviso: no se pudo copiar a finalistas: {e}")
        if self.in_testing:
            self.in_testing.mkdir(parents=True, exist_ok=True)
            try:
                shutil.move(str(ex5), str(self.in_testing / ex5.name))
            except OSError as e:
                self.log(f"[{ex5.stem}] aviso: no se pudo mover a en-testeo: {e}")

    @staticmethod
    def _write_set(path: Path, inputs: list) -> None:
        lines = [f"{n}={_fmt_input(v)}" for n, v in inputs if v is not None]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # --- leaderboard -------------------------------------------------------

    def write_leaderboard(self) -> tuple[Path, Path]:
        bots = self.state["bots"]
        rows = []
        for name, st in bots.items():
            final = (st.get("round3") or {}).get("final_metrics") or {}
            r2 = st.get("round2") or {}
            rows.append({
                "name": name,
                "verdict": st.get("verdict"),
                "reason": st.get("reason"),
                "best_symbol": (st.get("round1") or {}).get("best_symbol"),
                "r2_profit": r2.get("net_profit"),
                "final_profit": final.get("net_profit"),
                "final_dd_pct": final.get("drawdown_max_pct"),
                "lr": r2.get("lr_correlation"),
            })
        rank_key = lambda r: (r["verdict"] == "finalist",
                              r["final_profit"] if isinstance(r["final_profit"], (int, float)) else
                              (r["r2_profit"] if isinstance(r["r2_profit"], (int, float)) else float("-inf")))
        rows.sort(key=rank_key, reverse=True)

        stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
        json_out = self.output_dir / f"leaderboard_{stamp}.json"
        md_out = self.output_dir / f"leaderboard_{stamp}.md"
        json_out.write_text(json.dumps(rows, indent=2, ensure_ascii=False),
                            encoding="utf-8")
        md_out.write_text(self._render_leaderboard_md(rows), encoding="utf-8")
        return md_out, json_out

    @staticmethod
    def _render_leaderboard_md(rows: list[dict]) -> str:
        def f(v):
            if v is None:
                return "—"
            return f"{v:,.2f}" if isinstance(v, float) else str(v)
        lines = [
            "# MT5 Robot Tester — Leaderboard",
            "",
            f"Generado: {dt.datetime.now():%Y-%m-%d %H:%M:%S}",
            "",
            "| # | Bot | Veredicto | Mejor par | Bº R2 | Bº final | DD final % | LR | Motivo |",
            "|---|-----|-----------|-----------|------:|---------:|-----------:|---:|--------|",
        ]
        for i, r in enumerate(rows, 1):
            lines.append(
                f"| {i} | {r['name']} | {r.get('verdict','')} | "
                f"{r.get('best_symbol') or '—'} | {f(r.get('r2_profit'))} | "
                f"{f(r.get('final_profit'))} | {f(r.get('final_dd_pct'))} | "
                f"{f(r.get('lr'))} | {r.get('reason','')} |")
        lines.append("")
        return "\n".join(lines)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------

def discover_bots(candidates_dir: Path) -> list[Path]:
    bots = sorted([*candidates_dir.glob("*.ex5"), *candidates_dir.glob("*.mq5")])
    # Prefer compiled .ex5 when both exist for the same stem.
    seen = {}
    for b in bots:
        seen.setdefault(b.stem, b)
        if b.suffix == ".ex5":
            seen[b.stem] = b
    return sorted(seen.values())


def load_config(path: Optional[str]) -> dict:
    if not path:
        return {}
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main(argv: Optional[list[str]] = None) -> int:
    # Windows consoles default to cp1252; make our output UTF-8 safe.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass

    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", help="JSON de configuración del pipeline.")
    parser.add_argument("--candidates-dir", help="Carpeta de bots candidatos (override).")
    parser.add_argument("--output-dir", default="reports/mt5_pipeline")
    parser.add_argument("--terminal-path", default=None)
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument("--portable", action="store_true")
    parser.add_argument("--resume", action="store_true",
                        help="Continuar desde state.json sin repetir lo hecho.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Genera los .ini de la Ronda 1 sin lanzar MT5.")
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.candidates_dir:
        config.setdefault("folders", {})["candidates"] = args.candidates_dir

    candidates = config.get("folders", {}).get("candidates")
    if not candidates:
        print("error: falta la carpeta de candidatos (config folders.candidates o "
              "--candidates-dir)", file=sys.stderr)
        return 1
    candidates_dir = Path(candidates)
    if not candidates_dir.exists():
        print(f"error: no existe la carpeta de candidatos: {candidates_dir}",
              file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    timeout = args.timeout or config.get("timeout_seconds", DEFAULTS["timeout_seconds"])

    bots = discover_bots(candidates_dir)
    if not bots:
        print(f"error: no hay .ex5/.mq5 en {candidates_dir}", file=sys.stderr)
        return 1

    common = {**DEFAULTS["common"], **config.get("common", {})}
    expert_prefix = (config.get("experts_relpath") or experts_relpath(candidates_dir))

    if args.dry_run:
        ini_dir = output_dir / "mt5_ini"
        ini_dir.mkdir(parents=True, exist_ok=True)
        for ex5 in bots:
            expert = f"{expert_prefix}\\{ex5.name}" if expert_prefix else ex5.name
            ini = build_round1_ini(expert, common,
                                   (output_dir / "mt5_reports" / f"{ex5.stem}__R1").resolve())
            (ini_dir / f"{ex5.stem}__R1.ini").write_text(ini, encoding="utf-8")
        print(f"[dry-run] {len(bots)} bots -> INIs de Ronda 1 en {ini_dir}")
        print("Las Rondas 2/3 dependen de resultados reales; ejecuta sin --dry-run.")
        return 0

    terminal = find_terminal(args.terminal_path or config.get("terminal_path"))
    if terminal is None:
        print("error: no se encontró terminal64.exe. Usa --terminal-path, "
              "$MT5_TERMINAL_PATH o config.terminal_path.", file=sys.stderr)
        return 1

    pipe = Pipeline(config, output_dir, terminal, timeout, args.portable, args.resume)
    pipe.learn.bump_run()
    pipe.log(f"Terminal: {terminal}")
    pipe.log(f"Candidatos: {candidates_dir} ({len(bots)} bots)")

    for ex5 in bots:
        try:
            pipe.process_bot(ex5)
        except Exception as e:  # keep the loop resilient; record and continue
            pipe.log(f"[{ex5.stem}] ERROR inesperado: {e}")
            st = pipe.state["bots"].setdefault(ex5.stem, {})
            st.update({"status": "done", "verdict": "error", "reason": str(e)})
            pipe._save_state()

    pipe.learn.save(markdown_path=pipe._learn_md)
    md, js = pipe.write_leaderboard()
    pipe.log(f"Leaderboard: {md}")
    pipe.log(f"Learnings:   {pipe.learn.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
