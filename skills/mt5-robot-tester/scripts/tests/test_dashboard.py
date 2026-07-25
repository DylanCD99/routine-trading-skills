"""Tests for the dashboard status helpers."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dashboard import build_status, derive_bot_view  # noqa: E402


def test_derive_finalist():
    st = {"status": "done", "verdict": "finalist", "reason": "ok",
          "round1": {"best_symbol": "XAUUSD"},
          "round2": {"net_profit": 84.1, "lr_correlation": 0.95},
          "round3": {"per_param": [], "final_metrics": {"net_profit": 84.1,
                                                        "drawdown_max_pct": 0.25}}}
    v = derive_bot_view("Bot", st)
    assert v["phase"] == "done"
    assert v["passed"] is True
    assert v["best_symbol"] == "XAUUSD"
    assert v["final_dd_pct"] == 0.25


def test_derive_rejected_and_phase_progression():
    assert derive_bot_view("B", {"round1": {}})["phase"] == "R1"
    assert derive_bot_view("B", {"round1": {}, "round2": {}})["phase"] == "R2"
    assert derive_bot_view("B", {})["phase"] == "pending"
    r = derive_bot_view("B", {"status": "done", "verdict": "rejected",
                              "round1": {"reason": "R1: pocos positivos"}})
    assert r["passed"] is False
    assert r["verdict"] == "rejected"


def test_build_status_reads_state_and_folders(tmp_path):
    cand = tmp_path / "cand"; cand.mkdir()
    (cand / "A.ex5").write_text("x"); (cand / "B.ex5").write_text("x")
    fin = tmp_path / "fin"; fin.mkdir()
    (fin / "A.ex5").write_text("x")
    out = tmp_path / "out"; out.mkdir()
    (out / "state.json").write_text(
        '{"bots":{"A":{"status":"done","verdict":"finalist","round1":{},'
        '"round2":{},"round3":{"per_param":[]}},'
        '"B":{"status":"done","verdict":"rejected","round1":{}}}}',
        encoding="utf-8")
    (out / "run.log").write_text("linea1\nlinea2\n", encoding="utf-8")

    config = {"folders": {"candidates": str(cand), "in_testing": str(tmp_path / "none"),
                          "finalists": str(fin)}}
    s = build_status(config, out, running=True)
    assert s["running"] is True
    assert s["summary"]["finalists"] == 1
    assert s["summary"]["rejected"] == 1
    assert s["folders"]["candidates"] == ["A", "B"]
    assert s["folders"]["finalists"] == ["A"]
    assert s["log_tail"] == ["linea1", "linea2"]
