import pandas as pd

from research.registered_strategies import REGISTERED
from sim.tl.events import REQUIRED_COLUMNS, validate_fact_events
from sim.tl.funnel import funnel, opportunity_summary


def _events():
    return pd.DataFrame([
        {"event_id":"1","instrument":"EURUSD","timeframe":"1h","trendline_id":"tl1","event_type":"TOUCH","occurred_at":"2026-01-01T00:00:00Z","known_at":"2026-01-01T00:00:00Z","price":1.10,"trendline_price":1.10,"distance":0.0},
        {"event_id":"2","instrument":"EURUSD","timeframe":"1h","trendline_id":"tl1","event_type":"BREAK","occurred_at":"2026-01-01T01:00:00Z","known_at":"2026-01-01T01:00:00Z","price":1.09,"trendline_price":1.10,"distance":0.01},
        {"event_id":"3","instrument":"EURUSD","timeframe":"1h","trendline_id":"tl1","event_type":"RETEST","occurred_at":"2026-01-01T02:00:00Z","known_at":"2026-01-01T02:00:00Z","price":1.10,"trendline_price":1.10,"distance":0.0},
    ])


def test_fact_contract_contains_only_facts():
    df = _events()
    assert set(REQUIRED_COLUMNS).issubset(df.columns)
    assert validate_fact_events(df) == []


def test_fact_contract_rejects_strategy_opinions_and_future_known_time():
    df = _events()
    df.loc[0, "event_type"] = "BOUNCE"
    df.loc[1, "known_at"] = "2025-12-31T23:00:00Z"
    errors = validate_fact_events(df)
    assert any("strategy opinion" in x for x in errors)
    assert any("known_at precedes" in x for x in errors)


def test_funnel_and_opportunity_gate():
    df = pd.concat([_events()] * 200, ignore_index=True)
    df["event_id"] = [f"e{i}" for i in range(len(df))]
    f = funnel(df).set_index("stage")
    assert f.loc["TOUCH", "count"] == 200
    assert f.loc["BREAK", "count"] == 200
    assert f.loc["RETEST", "count"] == 200
    opp = opportunity_summary(df, min_sample=200)
    assert bool(opp.loc[opp.hypothesis.eq("bounce"), "eligible"].iloc[0])
    assert bool(opp.loc[opp.hypothesis.eq("breakout"), "eligible"].iloc[0])
    assert bool(opp.loc[opp.hypothesis.eq("breakout_retest"), "eligible"].iloc[0])


def test_registered_strategy_contracts_are_separate_from_events():
    assert set(REGISTERED) == {"bounce", "breakout", "breakout_retest"}
    assert REGISTERED["bounce"].no_break_bars == 5
    assert REGISTERED["breakout"].continuation_atr == 0.25
    assert REGISTERED["breakout_retest"].retest_max_bars == 12


def test_event_runner_keyword_matches_fact_event_api():
    import inspect
    from sim.tl.events import build_fact_events
    signature = inspect.signature(build_fact_events)
    assert "instrument" in signature.parameters
    assert "symbol" not in signature.parameters


def test_mixed_naive_and_aware_bar_index_is_normalized_in_event_builder():
    from sim.tl.events import _utc_index
    # object dtype on purpose: pandas will not BUILD a mixed DatetimeIndex, so
    # asking for one tested nothing but the fixture. This is the shape a mixed
    # index actually arrives in.
    mixed = pd.Index([
        pd.Timestamp("2026-01-01 00:00:00"),
        pd.Timestamp("2026-01-01 01:00:00", tz="UTC"),
    ], dtype=object)
    normalized = _utc_index(mixed)
    assert str(normalized.tz) == "UTC"
    assert list(normalized) == [
        pd.Timestamp("2026-01-01 00:00:00", tz="UTC"),
        pd.Timestamp("2026-01-01 01:00:00", tz="UTC"),
    ]
