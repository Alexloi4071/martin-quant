from __future__ import annotations

import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Mapping

from martin_quant.signals.journal import SignalJournal
from martin_quant.signals.models import SignalEvent

log = logging.getLogger(__name__)


class WebhookRequestProcessor:
    def __init__(
        self,
        journal_dir: str = "outputs/signals",
        shared_secret: str = "",
        send_telegram: bool = False,
    ) -> None:
        self.journal = SignalJournal(base_dir=journal_dir)
        self.shared_secret = shared_secret
        self.send_telegram = send_telegram

    def process(self, raw_body: str, headers: Mapping[str, str] | None = None) -> tuple[int, dict]:
        payload = self._parse_payload(raw_body)
        if self.shared_secret and payload.get("secret") != self.shared_secret:
            return 401, {"ok": False, "error": "invalid_secret"}

        event = SignalEvent.from_payload(payload)
        if not event.symbol:
            return 400, {"ok": False, "error": "missing_symbol"}

        self.journal.append_event(event)
        if self.send_telegram:
            self._send_telegram(event)
        return 202, {"ok": True, "event_id": event.event_id}

    @staticmethod
    def _parse_payload(raw_body: str) -> dict:
        if not raw_body.strip():
            return {}
        try:
            parsed = json.loads(raw_body)
            return parsed if isinstance(parsed, dict) else {"raw": parsed}
        except json.JSONDecodeError:
            return {"raw": raw_body}

    @staticmethod
    def _format_message(event: SignalEvent) -> str:
        lines = [
            f"*Signal Event* {event.symbol}",
            f"Direction: `{event.direction}`",
            f"Setup: `{event.setup_type or 'unknown'}`",
        ]
        if event.trigger_type:
            lines.append(f"Trigger: `{event.trigger_type}`")
        if event.timeframe:
            lines.append(f"Timeframe: `{event.timeframe}`")
        if event.price is not None:
            lines.append(f"Price: `{event.price}`")
        return "\n".join(lines)

    def _send_telegram(self, event: SignalEvent) -> None:
        try:
            from martin_quant.utils.alert_manager import AlertManager

            manager = AlertManager()
            if manager.is_enabled:
                manager.send_text(self._format_message(event))
        except Exception as exc:
            log.warning("WebhookRequestProcessor telegram send failed: %s", exc)


def run_webhook_server(
    host: str = "127.0.0.1",
    port: int = 8787,
    processor: WebhookRequestProcessor | None = None,
) -> None:
    active_processor = processor or WebhookRequestProcessor()

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length).decode("utf-8") if length else ""
            status, payload = active_processor.process(raw, headers=self.headers)
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args) -> None:  # noqa: A003
            log.info("WebhookServer: " + format, *args)

    server = ThreadingHTTPServer((host, port), Handler)
    log.info("Webhook server listening on http://%s:%s", host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()
