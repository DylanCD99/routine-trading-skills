"""Tests for the orchestrator's pure helpers and INI builders."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mt5_batch_tester import (  # noqa: E402
    build_backtest_ini, build_optimize_param_ini, build_round1_ini,
    compute_range, evaluate_finalist, evaluate_round2_profile, experts_relpath,
    inputs_from_passes, select_strategy_params,
)
from parse_mt5_optimization import parse_passes  # noqa: E402

DEPOSIT = 10000
GATES = {
    "finalist_min_profit_multiple": 4.0,
    "finalist_max_drawdown_pct": 12.0,
    "finalist_require_improvement": True,
}
COMMON = {"period": "H1", "from": "2020.01.01", "to": "2026.06.30", "model": 4,
          "deposit": 10000, "currency": "USD", "leverage": 100,
          "optimization_criterion": 0}


# --- ranges --------------------------------------------------------------

def test_compute_range_float():
    start, step, stop = compute_range(1.5, 0.5, 0.05)
    assert start == 0.75
    assert stop == 2.25
    assert abs(step - 0.075) < 1e-9


def test_compute_range_integer_period():
    start, step, stop = compute_range(86, 0.5, 0.05)
    assert (start, stop) == (43, 129)
    assert step == 4  # round(86*0.05)=4


def test_compute_range_zero_returns_none():
    assert compute_range(0, 0.5, 0.05) is None


def test_compute_range_small_int_step_floor_one():
    _, step, _ = compute_range(10, 0.5, 0.05)  # 0.5 -> round 0 -> floored to 1
    assert step == 1


# --- parameter selection -------------------------------------------------

def test_select_params_after_magic():
    names = ["FillType", "ForceFill", "CustomComment", "MagicNumber",
             "GannHiLoPeriod1", "PriceEntryMult1", "StopLossCoef1",
             "TrailingStopCoef1", "TrailingActCef1"]
    got = select_strategy_params(names, "MagicNumber", 6)
    assert got == ["GannHiLoPeriod1", "PriceEntryMult1", "StopLossCoef1",
                   "TrailingStopCoef1", "TrailingActCef1"]


def test_select_params_fallback_without_magic():
    names = ["A", "B", "C"]
    assert select_strategy_params(names, "MagicNumber", 6) == ["A", "B", "C"]


def test_inputs_from_passes_ordered():
    passes = parse_passes(Path(__file__).resolve().parent
                          / "fixtures" / "sample_opt.xml")
    inputs = inputs_from_passes(passes)
    names = [n for n, _ in inputs]
    assert names[0] == "MagicNumber"
    assert names[1] == "GannHiLoPeriod1"
    strat = select_strategy_params(names, "MagicNumber", 6)
    assert strat == ["GannHiLoPeriod1", "PriceEntryMult1", "StopLossCoef1",
                     "TrailingStopCoef1", "TrailingActCef1"]


# --- finalist decision ---------------------------------------------------

def test_finalist_passes():
    final = {"net_profit": 45000, "drawdown_max_pct": 10.0}
    ok, reason = evaluate_finalist(final, r2_profit=20000, deposit=DEPOSIT, gates=GATES)
    assert ok is True
    assert reason == "ok"


def test_finalist_fails_on_drawdown():
    final = {"net_profit": 45000, "drawdown_max_pct": 13.0}
    ok, reason = evaluate_finalist(final, 20000, DEPOSIT, GATES)
    assert ok is False
    assert "DD" in reason


def test_finalist_fails_without_improvement():
    final = {"net_profit": 45000, "drawdown_max_pct": 8.0}
    ok, reason = evaluate_finalist(final, r2_profit=50000, deposit=DEPOSIT, gates=GATES)
    assert ok is False
    assert "no mejora" in reason


def test_finalist_fails_below_4x():
    final = {"net_profit": 30000, "drawdown_max_pct": 8.0}
    ok, reason = evaluate_finalist(final, 20000, DEPOSIT, GATES)
    assert ok is False
    assert "beneficio" in reason


# --- round-2 profile -----------------------------------------------------

def test_round2_profile_all_pass():
    ref = {"min_profit_pct": 300, "max_drawdown_pct": 15,
           "min_pct_positive_months": 70, "require_all_years_positive": True,
           "min_lr_correlation": 0.80, "max_months_to_new_high": 3}
    metrics = {"profit_pct": 320, "drawdown_max_pct": 10, "pct_positive_months": 75,
               "all_years_positive": True, "lr_correlation": 0.9,
               "max_months_to_new_high": 2}
    res = evaluate_round2_profile(metrics, DEPOSIT, ref)
    assert res["all_passed"] is True


def test_round2_profile_fails_lr():
    ref = {"min_profit_pct": 300, "max_drawdown_pct": 15,
           "min_pct_positive_months": 70, "require_all_years_positive": True,
           "min_lr_correlation": 0.80, "max_months_to_new_high": 3}
    metrics = {"profit_pct": 320, "drawdown_max_pct": 10, "pct_positive_months": 75,
               "all_years_positive": True, "lr_correlation": 0.7,
               "max_months_to_new_high": 2}
    res = evaluate_round2_profile(metrics, DEPOSIT, ref)
    assert res["lr_correlation"] is False
    assert res["all_passed"] is False


# --- expert path ---------------------------------------------------------

def test_experts_relpath_under_experts():
    p = r"C:\Users\x\AppData\Roaming\MetaQuotes\Terminal\ABC\MQL5\Experts\bots candidatos"
    assert experts_relpath(p) == "bots candidatos"


def test_experts_relpath_nested():
    p = r"C:\MT5\MQL5\Experts\group\bots candidatos"
    assert experts_relpath(p) == "group\\bots candidatos"


# --- INI builders --------------------------------------------------------

def test_round1_ini_has_optimization_3(tmp_path):
    ini = build_round1_ini("bots candidatos\\Bot.ex5", COMMON, tmp_path / "r1")
    assert "Optimization=3" in ini
    assert "OptimizationCriterion=0" in ini
    assert "Model=4" in ini
    assert "ShutdownTerminal=1" in ini
    assert "FromDate=2020.01.01" in ini


def test_backtest_ini_optimization_0_with_inputs(tmp_path):
    ini = build_backtest_ini("bots candidatos\\Bot.ex5", "US100.cash", COMMON,
                             tmp_path / "r2", inputs=[("StopLossCoef1", 1.0)])
    assert "Optimization=0" in ini
    assert "Symbol=US100.cash" in ini
    assert "[TesterInputs]" in ini
    assert "StopLossCoef1=1" in ini


def test_optimize_param_ini_range_syntax(tmp_path):
    strat = [("GannHiLoPeriod1", 86), ("PriceEntryMult1", 1.5)]
    ini = build_optimize_param_ini("bots candidatos\\Bot.ex5", "US100.cash", COMMON,
                                   tmp_path / "r3", strat, "GannHiLoPeriod1",
                                   (43, 4, 129))
    assert "Optimization=1" in ini
    assert "GannHiLoPeriod1=43||43||4||129||Y" in ini
    assert "PriceEntryMult1=1.5" in ini  # fixed
