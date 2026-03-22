#!/usr/bin/env python
"""Check external service connectivity for martin-quant.

Usage:
    python scripts/check_connections.py
    python scripts/check_connections.py --telegram-updates
    python scripts/check_connections.py --env-file .env
"""
from __future__ import annotations

import argparse
import json
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

import os


DEFAULT_TIMEOUT = 15


@dataclass
class CheckResult:
    service: str
    status: str
    detail: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check API keys, Telegram chat access, and IBKR connectivity from .env."
    )
    parser.add_argument("--env-file", default=".env", help="Path to env file to load")
    parser.add_argument(
        "--telegram-updates",
        action="store_true",
        help="Also fetch Telegram getUpdates and print discovered chat ids",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help="HTTP timeout in seconds",
    )
    return parser.parse_args()


def load_env(env_file: str) -> None:
    if load_dotenv is None:
        raise RuntimeError("python-dotenv is required to load .env for this script.")
    path = Path(env_file)
    if not path.exists():
        raise FileNotFoundError(f"Env file not found: {path}")
    load_dotenv(dotenv_path=path, override=True)


def mask_value(value: str) -> str:
    if not value:
        return ""
    if len(value) <= 8:
        return "*" * len(value)
    return f"{value[:4]}{'*' * (len(value) - 8)}{value[-4:]}"


def check_finnhub(timeout: int) -> CheckResult:
    api_key = os.getenv("FINNHUB_API_KEY", "").strip()
    if not api_key:
        return CheckResult("Finnhub", "skipped", "FINNHUB_API_KEY missing")

    try:
        resp = requests.get(
            "https://finnhub.io/api/v1/quote",
            params={"symbol": "AAPL", "token": api_key},
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("c") is None:
            return CheckResult("Finnhub", "unexpected_response", "Missing quote field")
        return CheckResult("Finnhub", "ok", "AAPL quote returned")
    except Exception as exc:
        return CheckResult("Finnhub", "failed", str(exc))


def check_polygon(timeout: int) -> CheckResult:
    api_key = os.getenv("POLYGON_API_KEY", "").strip()
    if not api_key:
        return CheckResult("Polygon", "skipped", "POLYGON_API_KEY missing")

    try:
        resp = requests.get(
            "https://api.polygon.io/v2/aggs/ticker/AAPL/prev",
            params={"adjusted": "true", "apiKey": api_key},
            timeout=timeout,
        )
        resp.raise_for_status()
        raw = resp.text
        payload = json.loads(raw) if isinstance(raw, str) else resp.json()
        if payload.get("status") == "OK" and payload.get("resultsCount", 0) >= 1:
            return CheckResult("Polygon", "ok", "Prev aggregate endpoint responded")
        return CheckResult("Polygon", "unexpected_response", raw[:180])
    except Exception as exc:
        return CheckResult("Polygon", "failed", str(exc))


def check_fmp(timeout: int) -> CheckResult:
    api_key = os.getenv("FMP_API_KEY", "").strip()
    if not api_key:
        return CheckResult("FMP", "skipped", "FMP_API_KEY missing")

    try:
        resp = requests.get(
            "https://financialmodelingprep.com/stable/quote",
            params={"symbol": "AAPL", "apikey": api_key},
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
        if isinstance(payload, list) and payload:
            return CheckResult("FMP", "ok", "Stable quote endpoint responded")
        return CheckResult("FMP", "unexpected_response", "No rows returned")
    except Exception as exc:
        return CheckResult("FMP", "failed", str(exc))


def check_benzinga(timeout: int) -> CheckResult:
    api_key = os.getenv("BENZINGA_API_KEY", "").strip()
    if not api_key:
        return CheckResult("Benzinga", "skipped", "BENZINGA_API_KEY missing")

    try:
        resp = requests.get(
            "https://api.benzinga.com/api/v2/news",
            params={"token": api_key, "page": 0, "pagesize": 1},
            headers={"Accept": "application/json"},
            timeout=timeout,
        )
        resp.raise_for_status()
        return CheckResult("Benzinga", "ok", "News endpoint responded")
    except Exception as exc:
        return CheckResult("Benzinga", "failed", str(exc))


def telegram_token_diagnostics(token: str) -> str:
    format_ok = token.count(":") == 1
    has_whitespace = any(ch.isspace() for ch in token)
    return (
        f"formatOk={format_ok}; hasWhitespace={has_whitespace}; "
        f"masked={mask_value(token)}"
    )


def check_telegram_bot(timeout: int) -> CheckResult:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return CheckResult("TelegramBot", "skipped", "TELEGRAM_BOT_TOKEN missing")

    diag = telegram_token_diagnostics(token)
    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getMe",
            timeout=timeout,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("ok") is True:
            return CheckResult("TelegramBot", "ok", f"{diag}; Bot token valid")
        return CheckResult("TelegramBot", "unexpected_response", f"{diag}; {payload}")
    except Exception as exc:
        return CheckResult("TelegramBot", "failed", f"{diag}; err={exc}")


def check_telegram_chat(timeout: int) -> CheckResult:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        return CheckResult(
            "TelegramChat", "skipped", "TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID missing"
        )

    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getChat",
            params={"chat_id": chat_id},
            timeout=timeout,
        )
        if resp.status_code >= 400:
            return CheckResult("TelegramChat", "failed", f"HTTP {resp.status_code}: {resp.text[:160]}")
        payload = resp.json()
        if payload.get("ok") is True:
            return CheckResult("TelegramChat", "ok", "Chat id accessible")
        return CheckResult("TelegramChat", "unexpected_response", str(payload))
    except Exception as exc:
        return CheckResult("TelegramChat", "failed", str(exc))


def get_telegram_updates(timeout: int) -> tuple[CheckResult, list[dict[str, Any]]]:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        return CheckResult("TelegramUpdates", "skipped", "TELEGRAM_BOT_TOKEN missing"), []

    try:
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            timeout=timeout,
        )
        if resp.status_code >= 400:
            return (
                CheckResult("TelegramUpdates", "failed", f"HTTP {resp.status_code}: {resp.text[:160]}"),
                [],
            )
        payload = resp.json()
        if payload.get("ok") is not True:
            return CheckResult("TelegramUpdates", "unexpected_response", str(payload)), []

        discovered: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for item in payload.get("result", []):
            for key in ("message", "channel_post", "edited_message", "my_chat_member"):
                msg = item.get(key)
                if not isinstance(msg, dict):
                    continue
                chat = msg.get("chat", {})
                chat_id = str(chat.get("id", ""))
                if not chat_id:
                    continue
                title = chat.get("title") or chat.get("username") or chat.get("first_name") or ""
                chat_type = str(chat.get("type", ""))
                dedupe_key = (chat_id, chat_type)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                discovered.append(
                    {
                        "chat_id": chat_id,
                        "type": chat_type,
                        "title": title,
                    }
                )

        return CheckResult("TelegramUpdates", "ok", f"Found {len(discovered)} chat(s)"), discovered
    except Exception as exc:
        return CheckResult("TelegramUpdates", "failed", str(exc)), []


def check_ibkr() -> CheckResult:
    host = os.getenv("IBKR_HOST", "127.0.0.1").strip() or "127.0.0.1"
    port_raw = os.getenv("IBKR_PORT", "4002").strip() or "4002"
    try:
        port = int(port_raw)
    except ValueError:
        return CheckResult("IBKRGateway", "failed", f"Invalid IBKR_PORT: {port_raw}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3.0)
    try:
        sock.connect((host, port))
        return CheckResult("IBKRGateway", "ok", f"TCP {host}:{port} reachable")
    except Exception as exc:
        return CheckResult("IBKRGateway", "failed", f"TCP {host}:{port} not reachable ({exc})")
    finally:
        sock.close()


def print_results(results: list[CheckResult]) -> None:
    service_width = max(len(r.service) for r in results) if results else 10
    status_width = max(len(r.status) for r in results) if results else 6
    print(f"{'Service':<{service_width}}  {'Status':<{status_width}}  Detail")
    print(f"{'-' * service_width}  {'-' * status_width}  {'-' * 60}")
    for row in results:
        print(f"{row.service:<{service_width}}  {row.status:<{status_width}}  {row.detail}")


def print_telegram_updates(chats: list[dict[str, Any]]) -> None:
    print("\nTelegram chat candidates from getUpdates:")
    if not chats:
        print("  No chats found. Send the bot a message first, then rerun with --telegram-updates.")
        return

    print("  chat_id           type       title")
    print("  ----------------  ---------  ------------------------------")
    for chat in chats:
        chat_id = str(chat.get("chat_id", ""))
        chat_type = str(chat.get("type", ""))
        title = str(chat.get("title", ""))
        print(f"  {chat_id:<16}  {chat_type:<9}  {title}")


def main() -> int:
    args = parse_args()

    try:
        load_env(args.env_file)
    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1

    results = [
        check_finnhub(args.timeout),
        check_polygon(args.timeout),
        check_fmp(args.timeout),
        check_benzinga(args.timeout),
        check_telegram_bot(args.timeout),
        check_telegram_chat(args.timeout),
        check_ibkr(),
    ]

    chats: list[dict[str, Any]] = []
    if args.telegram_updates:
        update_result, chats = get_telegram_updates(args.timeout)
        results.append(update_result)

    print_results(results)

    if args.telegram_updates:
        print_telegram_updates(chats)

    return 0 if all(r.status in {"ok", "skipped"} for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
