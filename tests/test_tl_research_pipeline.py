import pandas as pd
import pytest

from research.tl_pipeline import event_funnel, run_edge_gates, validate_event_contract


def _events():
    return pd.DataFrame([
        {"event_id":"e1","instrument":"EURUSD","timeframe":"1h","trendline_id":"tl1","event_type":"TOUCH","occurred_at":"2026-01-01T00:00:00Z","known_at":"2026-01-01T00:00:00Z","price":1.1,"trendline_price":1.1,"distance":0.0},
        {"event_id":"e2","instrument":"EURUSD","timeframe":"1h","trendline_id":"tl1","event_type":"BREAK","occurred_at":"2026-01-01T01:00:00Z","known_at":"2026-01-01T01:00:00Z","price":1.09,"trendline_price":1.1,"distance":0.01},
        {"event_id":"e3","instrument":"EURUSD","timeframe":"1h","trendline_id":"tl1","event_type":"RETEST","occurred_at":"2026-01-01T02:00:00Z","known_at":"2026-01-01T02:00:00Z","price":1.1,"trendline_price":1.1,"distance":0.0},
    ])


def test_fact_event_contract_passes():
    assert validate_event_contract(_events()) == []


def test_fact_event_contract_rejects_strategy_opinion():
    df = _events()
    df.loc[0, "event_type"] = "BOUNCE"
    errors = validate_event_contract(df)
    assert any("strategy opinions" in e for e in errors)


def test_known_at_cannot_precede_occurred_at():
    df = _events()
    df.loc[0, "known_at"] = "2025-12-31T23:59:00Z"
    assert any("known_at precedes" in e for e in validate_event_contract(df))


def test_funnel_counts_fact_events():
    f = event_funnel(_events()).set_index("event_type")
    assert f.loc["TOUCH", "count"] == 1
    assert f.loc["BREAK", "count"] == 1
    assert f.loc["RETEST", "count"] == 1
    assert f.loc["INVALIDATED", "count"] == 0


def test_edge_gates_pass_only_when_registered_conditions_hold():
    gates = run_edge_gates({"n": 250, "avg_r": .1, "profit_factor": 1.2,
                            "time_shift_observed": .12, "time_shift_p95": .10})
    assert all(g.passed for g in gates)


def test_edge_gates_fail_small_sample_and_negative_expectancy():
    gates = run_edge_gates({"n": 50, "avg_r": -.1, "profit_factor": .8,
                            "time_shift_observed": .02, "time_shift_p95": .10})
    assert not all(g.passed for g in gates)
    assert any(g.name == "sample_floor" and not g.passed for g in gates)
    assert any(g.name == "avg_R" and not g.passed for g in gates)
