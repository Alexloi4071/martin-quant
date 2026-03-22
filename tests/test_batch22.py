from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace


class TestSignalPipeline:
    def test_export_scan_candidates_writes_split_outputs(self, tmp_path):
        from martin_quant.signals import export_scan_candidates

        results = [
            SimpleNamespace(
                symbol="NVDA",
                direction="long",
                setup_type="pullback",
                setup_score=0.72,
                total_score=0.81,
                regime="BULL",
                sector="semiconductors",
                entry_price=910.5,
                stop_price=892.0,
                target_price=966.0,
                avwap_score=0.2,
                sector_bonus=0.15,
                regime_weight=1.0,
                entry_note="avwap_support",
            ),
            SimpleNamespace(
                symbol="TSLA",
                direction="short",
                setup_type="short_resistance_reversal",
                setup_score=0.69,
                total_score=0.78,
                regime="BEAR",
                sector="consumer_discretionary",
                entry_price=166.2,
                stop_price=171.4,
                target_price=155.8,
                avwap_score=0.1,
                sector_bonus=0.15,
                regime_weight=0.3,
                entry_note="short_bias, vwap_fail",
            ),
        ]

        paths = export_scan_candidates(results, out_dir=str(tmp_path), as_of="2026-03-19")
        assert Path(paths["json"]).exists()
        assert Path(paths["csv"]).exists()
        assert Path(paths["long_txt"]).read_text(encoding="utf-8").strip() == "NVDA"
        assert Path(paths["short_txt"]).read_text(encoding="utf-8").strip() == "TSLA"

        payload = json.loads(Path(paths["json"]).read_text(encoding="utf-8"))
        assert payload["count"] == 2
        assert payload["long_count"] == 1
        assert payload["short_count"] == 1

    def test_signal_journal_round_trip(self, tmp_path):
        from martin_quant.signals.journal import SignalJournal
        from martin_quant.signals.models import SignalEvent

        journal = SignalJournal(base_dir=str(tmp_path))
        event = SignalEvent(
            event_id="evt-1",
            received_at="2026-03-19T00:00:00+00:00",
            source="test",
            symbol="AMD",
            direction="long",
            setup_type="pullback",
            trigger_type="reclaim",
            timeframe="5",
            price=123.45,
        )
        journal.append_event(event)
        items = journal.load_events()
        assert len(items) == 1
        assert items[0].event_id == "evt-1"
        assert items[0].symbol == "AMD"

    def test_webhook_processor_secret_and_storage(self, tmp_path):
        from martin_quant.signals import WebhookRequestProcessor

        processor = WebhookRequestProcessor(journal_dir=str(tmp_path), shared_secret="abc123")
        status, payload = processor.process('{"secret":"wrong","symbol":"NVDA"}')
        assert status == 401
        assert payload["ok"] is False

        status, payload = processor.process(
            '{"secret":"abc123","source":"tradingview","symbol":"NVDA","direction":"short","setup":"short_resistance","trigger":"prev_hour_low_break","price":901.5}'
        )
        assert status == 202
        assert payload["ok"] is True

        events = (tmp_path / "events.jsonl").read_text(encoding="utf-8").splitlines()
        assert len(events) == 1
        stored = json.loads(events[0])
        assert stored["symbol"] == "NVDA"
        assert stored["direction"] == "short"


class TestDirectionAwareRegimeFilter:
    def test_bear_keeps_longs_blocked_but_allows_shorts(self):
        from martin_quant.regime.sector_regime_filter import SectorRegimeFilter

        flt = SectorRegimeFilter()
        assert flt.allow("technology", "BEAR") is False
        assert flt.allow_short("technology", "BEAR") is True
        assert flt.sector_score_bonus_short("technology", "BEAR") == 0.15
