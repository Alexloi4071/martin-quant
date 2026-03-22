from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))


def load_update_module():
    module_path = ROOT / "scripts" / "update_universe_and_data.py"
    spec = importlib.util.spec_from_file_location("update_universe_and_data", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def sample_frame(close_value: float = 10.0) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "timestamp": pd.to_datetime(["2026-03-18T13:30:00Z"]),
            "open": [close_value - 0.5],
            "high": [close_value + 0.5],
            "low": [close_value - 1.0],
            "close": [close_value],
            "volume": [1_000_000],
        }
    )


def test_write_symbol_parquets_keeps_previous_file_on_temp_write_failure(monkeypatch, tmp_path):
    from martin_quant.data.providers.finnhub_provider import FinnhubProvider

    original_frames = {tf: sample_frame(10.0) for tf in ("1d", "1h", "15m")}
    FinnhubProvider.write_symbol_parquets(tmp_path, "AAA", original_frames)

    real_to_parquet = pd.DataFrame.to_parquet

    def failing_to_parquet(self, path, *args, **kwargs):
        if str(path).endswith(".tmp"):
            raise RuntimeError("simulated parquet temp write failure")
        return real_to_parquet(self, path, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_parquet", failing_to_parquet)

    with pytest.raises(RuntimeError, match="simulated parquet temp write failure"):
        FinnhubProvider.write_symbol_parquets(tmp_path, "AAA", {"1d": sample_frame(20.0)})

    preserved = pd.read_parquet(tmp_path / "1d" / "AAA.parquet")
    assert preserved["close"].tolist() == [10.0]
    assert all(path.suffix != ".tmp" for path in (tmp_path / "1d").iterdir())


def test_fetch_market_frames_uses_finnhub_fallback_for_missing_timeframes(monkeypatch):
    module = load_update_module()

    polygon_calls: list[tuple[str, int, str]] = []
    yfinance_calls: list[tuple[str, str]] = []

    def fake_polygon(symbol, multiplier, timespan, start_date, end_date, api_key, timeout=30):
        polygon_calls.append((symbol, multiplier, timespan))
        if timespan == "day":
            return sample_frame(20.0)
        raise RuntimeError("HTTP Error 403: Forbidden")

    def fake_yfinance(symbol, interval, period):
        yfinance_calls.append((symbol, interval))
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    class DummyFinnhubProvider:
        def __init__(self):
            self.calls: list[tuple[str, int, int, int]] = []

        def fetch_ohlcv_frames(self, symbol, daily_days=450, hourly_days=120, m15_days=45):
            self.calls.append((symbol, daily_days, hourly_days, m15_days))
            return {
                "1d": sample_frame(30.0),
                "1h": sample_frame(31.0),
                "15m": sample_frame(32.0),
            }

    provider = DummyFinnhubProvider()
    monkeypatch.setattr(module, "fetch_polygon_ohlcv", fake_polygon)
    monkeypatch.setattr(module, "fetch_yfinance_ohlcv", fake_yfinance)

    frames = module.fetch_market_frames(
        symbol="AAA",
        api_key="polygon-secret",
        daily_days=90,
        hourly_days=30,
        m15_days=10,
        finnhub_provider=provider,
    )

    assert frames["1d"]["close"].tolist() == [20.0]
    assert frames["1h"]["close"].tolist() == [31.0]
    assert frames["15m"]["close"].tolist() == [32.0]
    assert provider.calls == [("AAA", 90, 30, 10)]
    assert polygon_calls == [("AAA", 1, "day"), ("AAA", 1, "hour"), ("AAA", 15, "minute")]
    assert yfinance_calls == [("AAA", "60m"), ("AAA", "15m")]



def test_fetch_market_frames_uses_twelvedata_after_other_fallbacks_fail(monkeypatch):
    module = load_update_module()

    def failing_polygon(symbol, multiplier, timespan, start_date, end_date, api_key, timeout=30):
        raise RuntimeError("HTTP Error 403: Forbidden")

    def empty_yfinance(symbol, interval, period):
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])

    twelvedata_calls: list[tuple[str, str, int, str]] = []

    def fake_twelvedata(symbol, interval, outputsize, api_key, timeout=30):
        twelvedata_calls.append((symbol, interval, outputsize, api_key))
        close_map = {"1day": 41.0, "1h": 42.0, "15min": 43.0}
        return sample_frame(close_map[interval])

    class DummyFinnhubProvider:
        def fetch_ohlcv_frames(self, symbol, daily_days=450, hourly_days=120, m15_days=45):
            raise RuntimeError("HTTP Error 403: Forbidden")

    monkeypatch.setattr(module, "fetch_polygon_ohlcv", failing_polygon)
    monkeypatch.setattr(module, "fetch_yfinance_ohlcv", empty_yfinance)
    monkeypatch.setattr(module, "fetch_twelvedata_ohlcv", fake_twelvedata)
    monkeypatch.setenv("TWELVEDATA_API_KEY", "td-secret")

    frames = module.fetch_market_frames(
        symbol="AAA",
        api_key="polygon-secret",
        daily_days=90,
        hourly_days=30,
        m15_days=10,
        finnhub_provider=DummyFinnhubProvider(),
    )

    assert frames["1d"]["close"].tolist() == [41.0]
    assert frames["1h"]["close"].tolist() == [42.0]
    assert frames["15m"]["close"].tolist() == [43.0]
    assert [call[1] for call in twelvedata_calls] == ["1day", "1h", "15min"]
def test_main_resume_skips_complete_symbols_and_refetches_incomplete(monkeypatch, tmp_path):
    from martin_quant.data.providers.finnhub_provider import FinnhubProvider as RealFinnhubProvider

    module = load_update_module()
    monkeypatch.chdir(tmp_path)

    data_root = tmp_path / "data" / "us_equities"
    progress_dir = tmp_path / "outputs" / "refresh"

    complete_frames = {tf: sample_frame(11.0) for tf in module.EXPECTED_TIMEFRAMES}
    RealFinnhubProvider.write_symbol_parquets(data_root, "AAA", complete_frames)
    RealFinnhubProvider.write_symbol_parquets(data_root, "BBB", {"1d": sample_frame(12.0)})

    cfg = {
        "providers": {
            "finviz": {
                "screens": [{"name": "growth", "filters": [], "enabled": True}],
            },
            "finnhub": {},
        },
        "universe": {"benchmark_symbol": "AAA"},
        "data": {"provider": {"params": {"data_root": str(data_root)}}},
    }

    class DummyFinvizProvider:
        def __init__(self, config):
            self.config = config

        def screen_many(self, screens, dedupe=False):
            return pd.DataFrame(
                {
                    "symbol": ["AAA", "BBB"],
                    "screen_name": ["growth", "growth"],
                }
            )

    class DummyFinnhubProvider:
        def __init__(self, config):
            self.config = config
            self.api_key = "finnhub-secret"

        def build_metadata_frame(self, symbols):
            return pd.DataFrame()

        def build_earnings_frame(self, symbols, from_date=None, to_date=None):
            return pd.DataFrame()

        @staticmethod
        def write_symbol_parquets(data_root, symbol, frames):
            return RealFinnhubProvider.write_symbol_parquets(data_root, symbol, frames)

    fetch_calls: list[str] = []

    def fake_fetch_market_frames(symbol, api_key, daily_days, hourly_days, m15_days, finnhub_provider=None):
        fetch_calls.append(symbol)
        return {tf: sample_frame(30.0) for tf in module.EXPECTED_TIMEFRAMES}

    args = argparse.Namespace(
        config="config/research.yaml",
        data_root=str(data_root),
        progress_dir=str(progress_dir),
        max_symbols=10,
        per_screen_cap=40,
        earnings_lookahead_days=45,
        daily_days=30,
        hourly_days=10,
        m15_days=5,
        resume=True,
        allow_partial_frames=False,
    )

    monkeypatch.setattr(module, "load_env_file", lambda: None)
    monkeypatch.setattr(module, "parse_args", lambda: args)
    monkeypatch.setattr(module, "load_yaml", lambda path: cfg)
    monkeypatch.setattr(module, "FinvizProvider", DummyFinvizProvider)
    monkeypatch.setattr(module, "FinnhubProvider", DummyFinnhubProvider)
    monkeypatch.setattr(module, "fetch_market_frames", fake_fetch_market_frames)
    monkeypatch.setattr(module, "require_env", lambda name: "polygon-secret")

    module.main()

    assert fetch_calls == ["BBB"]
    assert set(module.existing_symbol_timeframes(data_root, "BBB")) == set(module.EXPECTED_TIMEFRAMES)

    state_df = pd.read_csv(progress_dir / "refresh_state.csv")
    state_df["symbol"] = state_df["symbol"].astype(str).str.upper()
    state = state_df.set_index("symbol").to_dict(orient="index")
    assert state["AAA"]["last_action"] == "resume_skip"
    assert state["BBB"]["status"] == "ok"
    assert int(state["BBB"]["attempts"]) == 1

    skipped_df = pd.read_csv(progress_dir / "download_skipped.csv")
    assert skipped_df["symbol"].tolist() == ["AAA"]

    progress_events = [
        json.loads(line)
        for line in (progress_dir / "symbol_progress.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert any(event["event"] == "resume_skip" and event["symbol"] == "AAA" for event in progress_events)
    assert any(event["event"] == "ok" and event["symbol"] == "BBB" for event in progress_events)



