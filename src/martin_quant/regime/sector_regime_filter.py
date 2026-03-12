"""sector_regime_filter.py

Sector Regime Filter — Batch 16 新增
=====================================
Martin Luk 策略：
  - 不是每個 sector 在每個 regime 都適合做多
  - BULL regime:  偏向 Tech / Semi / Consumer Discretionary
  - CHOPPY regime: 偏向 Defensive (Health / Utilities / Staples)
  - BEAR regime:  現金 or Short

功能:
  SectorRegimeFilter.allow(sector, regime) → bool
  SectorRegimeFilter.rank_sectors(regime)  → list[str] (best to worst)
  SectorRegimeFilter.filter_watchlist(watchlist, regime) → list filtered

  watchlist 每個 item 需有 'symbol' 和 'sector' 欄位
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional
import logging

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Sector → ETF 映射（方便從 yfinance / provider 抓 sector 動能）
# ---------------------------------------------------------------------------

SECTOR_ETF_MAP: dict[str, str] = {
    "technology":             "XLK",
    "semiconductors":         "SOXX",
    "consumer_discretionary": "XLY",
    "communication":          "XLC",
    "financials":             "XLF",
    "industrials":            "XLI",
    "materials":              "XLB",
    "energy":                 "XLE",
    "healthcare":             "XLV",
    "utilities":              "XLU",
    "consumer_staples":       "XLP",
    "real_estate":            "XLRE",
}

# ---------------------------------------------------------------------------
# Regime → Preferred / Allowed / Avoid sectors
# ---------------------------------------------------------------------------

REGIME_SECTOR_CONFIG: dict[str, dict] = {
    "BULL": {
        "preferred": [
            "technology", "semiconductors", "consumer_discretionary",
            "communication", "financials", "industrials",
        ],
        "allowed": [
            "materials", "energy", "healthcare",
        ],
        "avoid": [
            "utilities", "consumer_staples", "real_estate",
        ],
    },
    "WEAK_BULL": {
        "preferred": [
            "technology", "semiconductors", "healthcare", "financials",
        ],
        "allowed": [
            "consumer_discretionary", "communication", "industrials",
            "energy", "materials",
        ],
        "avoid": [
            "utilities", "real_estate",
        ],
    },
    "CHOPPY": {
        "preferred": [
            "healthcare", "consumer_staples", "utilities",
        ],
        "allowed": [
            "financials", "energy",
        ],
        "avoid": [
            "technology", "semiconductors", "consumer_discretionary",
            "communication", "industrials", "materials", "real_estate",
        ],
    },
    "BEAR": {
        "preferred": [],
        "allowed": [],
        "avoid": list(SECTOR_ETF_MAP.keys()),  # avoid all — go to cash
    },
}


@dataclass
class SectorFilterResult:
    symbol: str
    sector: str
    regime: str
    allowed: bool
    category: str   # "preferred" | "allowed" | "avoid"
    reason: str


class SectorRegimeFilter:
    """
    Sector × Regime 過濾器。

    Usage:
        filt = SectorRegimeFilter()
        ok = filt.allow("technology", "BULL")   # True
        ok = filt.allow("utilities",  "BULL")   # False

        ranked = filt.rank_sectors("BULL")
        # ['technology', 'semiconductors', ...]

        filtered_wl = filt.filter_watchlist(
            watchlist=[{"symbol": "NVDA", "sector": "semiconductors"}, ...],
            regime="BULL",
        )
    """

    def __init__(self, custom_config: Optional[dict] = None) -> None:
        self._config = custom_config or REGIME_SECTOR_CONFIG

    def _normalize(self, text: str) -> str:
        """小寫 + 去空白 + 替換空格為底線"""
        return text.lower().strip().replace(" ", "_").replace("-", "_")

    def _get_regime_cfg(self, regime: str) -> dict:
        regime_norm = self._normalize(regime)
        # 找最接近的 regime key
        for key in self._config:
            if key.lower() == regime_norm:
                return self._config[key]
        # Fallback
        log.warning("SectorRegimeFilter: unknown regime '%s', defaulting to CHOPPY", regime)
        return self._config.get("CHOPPY", {"preferred": [], "allowed": [], "avoid": []})

    def allow(self, sector: str, regime: str) -> bool:
        """是否允許在此 regime 交易此 sector"""
        cfg = self._get_regime_cfg(regime)
        sector_norm = self._normalize(sector)
        avoid = [self._normalize(s) for s in cfg.get("avoid", [])]
        return sector_norm not in avoid

    def category(self, sector: str, regime: str) -> str:
        """返回 preferred / allowed / avoid"""
        cfg = self._get_regime_cfg(regime)
        s = self._normalize(sector)
        for cat in ("preferred", "allowed", "avoid"):
            if s in [self._normalize(x) for x in cfg.get(cat, [])]:
                return cat
        return "allowed"  # unknown sector defaults to allowed

    def sector_score_bonus(self, sector: str, regime: str) -> float:
        """
        回傳 setup 評分加成：
          preferred → +0.15
          allowed   → +0.00
          avoid     → -0.30
        """
        cat = self.category(sector, regime)
        return {"preferred": 0.15, "allowed": 0.0, "avoid": -0.30}.get(cat, 0.0)

    def rank_sectors(self, regime: str) -> list[str]:
        """從最適合到最不適合排序 sector"""
        cfg = self._get_regime_cfg(regime)
        return (
            list(cfg.get("preferred", []))
            + list(cfg.get("allowed", []))
            + list(cfg.get("avoid", []))
        )

    def filter_watchlist(
        self,
        watchlist: list[dict],
        regime: str,
        include_allowed: bool = True,
    ) -> list[SectorFilterResult]:
        """
        過濾 watchlist，返回允許交易的股票。

        Parameters
        ----------
        watchlist : list[dict]
            每個 dict 需有 'symbol' 和 'sector'
        regime : str
        include_allowed : bool
            True = 包含 "allowed" 類別；False = 只留 "preferred"

        Returns
        -------
        list[SectorFilterResult] — 僅包含允許交易的結果
        """
        results = []
        for item in watchlist:
            symbol = item.get("symbol", "")
            sector = item.get("sector", "")
            if not sector:
                # 未知 sector → 當 allowed 處理
                results.append(SectorFilterResult(
                    symbol=symbol, sector="unknown", regime=regime,
                    allowed=True, category="allowed",
                    reason="unknown_sector_defaulted_to_allowed",
                ))
                continue

            cat = self.category(sector, regime)
            allowed = cat in ("preferred", "allowed") if include_allowed else cat == "preferred"

            results.append(SectorFilterResult(
                symbol=symbol, sector=sector, regime=regime,
                allowed=allowed, category=cat,
                reason=f"{regime}_{cat}",
            ))

        # 只返回 allowed=True 的
        return [r for r in results if r.allowed]

    def get_preferred_etfs(self, regime: str) -> list[str]:
        """返回此 regime 下偏好的 sector ETFs"""
        cfg = self._get_regime_cfg(regime)
        preferred_sectors = cfg.get("preferred", [])
        return [
            SECTOR_ETF_MAP[self._normalize(s)]
            for s in preferred_sectors
            if self._normalize(s) in SECTOR_ETF_MAP
        ]
