"""alert_manager.py

Telegram Alert Manager
======================
Martin Quant 通知系統 — 把每日掃描信號 & 出場警示
即時推送到你的 Telegram 手機通知。

設定:
  .env 內設定:
    TELEGRAM_BOT_TOKEN=1234567890:ABCxxx
    TELEGRAM_CHAT_ID=987654321

Usage:
    from martin_quant.utils.alert_manager import AlertManager
    from martin_quant.daily_scan import DailyScanResult
    from martin_quant.risk.exit_manager import ExitSignal

    alert = AlertManager()
    alert.send_scan_result(result)
    alert.send_exit_signal(exit_sig)
    alert.send_emergency("Market crashed! SPY -4%")
"""
from __future__ import annotations

import os
import logging
import datetime
from typing import Optional, TYPE_CHECKING

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from martin_quant.daily_scan import DailyScanResult, TradeSignal
    from martin_quant.risk.exit_manager import ExitSignal


class AlertManager:
    """
    Telegram Bot 通知管理器。

    若 BOT_TOKEN / CHAT_ID 未設定，所有 send_* 方法靜默跳過（不 crash）。
    """

    def __init__(
        self,
        bot_token: Optional[str] = None,
        chat_id: Optional[str] = None,
    ) -> None:
        self.bot_token = bot_token or os.getenv("TELEGRAM_BOT_TOKEN", "")
        self.chat_id   = chat_id   or os.getenv("TELEGRAM_CHAT_ID", "")
        self._enabled  = bool(self.bot_token and self.chat_id)
        if not self._enabled:
            log.warning(
                "AlertManager: TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID not set. "
                "Alerts disabled."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_scan_result(self, result: "DailyScanResult") -> bool:
        """
        發送每日掃描摘要 + 前 5 個信號到 Telegram。
        """
        if not self._enabled:
            return False

        regime_emoji = {"bull": "🐂", "caution": "⚠️", "bear": "🐻"}.get(
            result.regime.value.lower().split()[0], "📊"
        )
        today = result.date
        lines = [
            f"📊 *Martin Quant Daily Scan*",
            f"📅 {today}",
            f"{regime_emoji} Regime: *{result.regime.value}* | Health: {result.market_health.health_state}",
            f"🎯 Signals: *{len(result.signals)}* | Watchlist: {result.watchlist_count}",
            "",
        ]

        # Top themes
        hot_themes = [
            t.theme for t in result.top_themes
            if t.momentum_state == "hot"
        ]
        if hot_themes:
            lines.append(f"🔥 Hot Themes: {', '.join(hot_themes[:3])}")
            lines.append("")

        # Signals
        if result.signals:
            lines.append("*Top Signals:*")
            for i, sig in enumerate(result.signals[:5], 1):
                direction_emoji = "📈" if sig.direction == "long" else "📉"
                lines.append(
                    f"{i}. {direction_emoji} *{sig.symbol}* `{sig.setup_type}`"
                )
                lines.append(
                    f"   Entry: `${sig.entry_price:.2f}` "
                    f"Stop: `${sig.stop_price:.2f}` "
                    f"R={sig.r_potential:.1f} Score={sig.total_score:.3f}"
                )
                if sig.notes:
                    lines.append(f"   _{sig.notes[:80]}_")
        else:
            lines.append("_No signals today._")

        lines.append("")
        lines.append(f"_Sent by Martin Quant @ {datetime.datetime.now().strftime('%H:%M')}_")

        return self._send("\n".join(lines))

    def send_exit_signal(self, signal: "ExitSignal") -> bool:
        """
        發送出場警示通知。
        """
        if not self._enabled:
            return False

        urgency_emoji = {
            "immediate": "🚨",
            "next_open": "⚡",
            "eod": "🔔",
        }.get(signal.urgency, "📢")

        exit_emoji = "📤" if signal.is_full_exit else "📊"
        pct_str = (
            "FULL EXIT" if signal.is_full_exit
            else f"PARTIAL {int(signal.exit_pct*100)}%"
        )

        lines = [
            f"{urgency_emoji} *EXIT SIGNAL — {signal.symbol}*",
            f"Type: `{signal.exit_type}` | {exit_emoji} {pct_str}",
            f"Price: `${signal.exit_price:.2f}` | R: `{signal.r_current:.1f}R`",
            f"Reason: _{signal.reason}_",
        ]
        if signal.notes:
            lines.append(f"Note: {signal.notes}")
        lines.append(f"Urgency: *{signal.urgency.upper()}*")

        return self._send("\n".join(lines))

    def send_text(self, message: str) -> bool:
        """發送純文字訊息"""
        return self._send(message) if self._enabled else False

    def send_emergency(self, message: str) -> bool:
        """緊急警報（附紅色標記）"""
        return self._send(f"🚨🚨🚨 EMERGENCY\n{message}") if self._enabled else False

    def send_daily_summary(
        self,
        date: str,
        wins: int,
        losses: int,
        total_r: float,
    ) -> bool:
        """每日交易總結"""
        if not self._enabled:
            return False
        pnl_emoji = "✅" if total_r >= 0 else "❌"
        msg = (
            f"{pnl_emoji} *Daily Summary {date}*\n"
            f"Wins: {wins} | Losses: {losses}\n"
            f"Total R: `{total_r:+.1f}R`"
        )
        return self._send(msg)

    # ------------------------------------------------------------------
    # Core send
    # ------------------------------------------------------------------

    def _send(self, text: str) -> bool:
        """底層 Telegram sendMessage API 呼叫"""
        try:
            import urllib.request
            import urllib.parse
            import json

            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = json.dumps({
                "chat_id":    self.chat_id,
                "text":       text[:4000],   # Telegram 4096 char limit
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }).encode("utf-8")

            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
                if result.get("ok"):
                    log.debug("Telegram sent OK")
                    return True
                else:
                    log.warning("Telegram error: %s", result)
                    return False
        except Exception as exc:
            log.error("AlertManager._send failed: %s", exc)
            return False

    @property
    def is_enabled(self) -> bool:
        return self._enabled
