"""Sector regime filter with separate long and short preferences."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional
import logging
import re

log = logging.getLogger(__name__)

SECTOR_ETF_MAP: dict[str, str] = {
    "technology": "XLK",
    "semiconductors": "SOXX",
    "consumer_discretionary": "XLY",
    "communication": "XLC",
    "financials": "XLF",
    "industrials": "XLI",
    "materials": "XLB",
    "energy": "XLE",
    "healthcare": "XLV",
    "utilities": "XLU",
    "consumer_staples": "XLP",
    "real_estate": "XLRE",
}

SECTOR_NORMALIZATION_ALIASES: dict[str, str] = {
    "technology": "technology",
    "information_technology": "technology",
    "semiconductors": "semiconductors",
    "consumer_discretionary": "consumer_discretionary",
    "retail": "consumer_discretionary",
    "automobiles": "consumer_discretionary",
    "auto_components": "consumer_discretionary",
    "hotels_restaurants_leisure": "consumer_discretionary",
    "textiles_apparel_luxury_goods": "consumer_discretionary",
    "communication": "communication",
    "communications": "communication",
    "communication_services": "communication",
    "media": "communication",
    "telecommunication": "communication",
    "financials": "financials",
    "financial_services": "financials",
    "banking": "financials",
    "insurance": "financials",
    "industrials": "industrials",
    "aerospace_defense": "industrials",
    "airlines": "industrials",
    "building": "industrials",
    "commercial_services_supplies": "industrials",
    "construction": "industrials",
    "distributors": "industrials",
    "electrical_equipment": "industrials",
    "logistics_transportation": "industrials",
    "professional_services": "industrials",
    "road_rail": "industrials",
    "trading_companies_distributors": "industrials",
    "materials": "materials",
    "chemicals": "materials",
    "packaging": "materials",
    "energy": "energy",
    "healthcare": "healthcare",
    "health_care": "healthcare",
    "biotechnology": "healthcare",
    "pharmaceuticals": "healthcare",
    "life_sciences_tools_services": "healthcare",
    "utilities": "utilities",
    "consumer_staples": "consumer_staples",
    "tobacco": "consumer_staples",
    "real_estate": "real_estate",
}

LONG_REGIME_SECTOR_CONFIG: dict[str, dict[str, list[str]]] = {
    "BULL": {
        "preferred": [
            "technology", "semiconductors", "consumer_discretionary",
            "communication", "financials", "industrials",
        ],
        "allowed": ["materials", "energy", "healthcare"],
        "avoid": ["utilities", "consumer_staples", "real_estate"],
    },
    "WEAK_BULL": {
        "preferred": ["technology", "semiconductors", "healthcare", "financials"],
        "allowed": [
            "consumer_discretionary", "communication", "industrials",
            "energy", "materials",
        ],
        "avoid": ["utilities", "real_estate"],
    },
    "CHOPPY": {
        "preferred": ["healthcare", "consumer_staples", "utilities"],
        "allowed": ["financials", "energy"],
        "avoid": [
            "technology", "semiconductors", "consumer_discretionary",
            "communication", "industrials", "materials", "real_estate",
        ],
    },
    "BEAR": {
        "preferred": [],
        "allowed": [],
        "avoid": list(SECTOR_ETF_MAP.keys()),
    },
}

SHORT_REGIME_SECTOR_CONFIG: dict[str, dict[str, list[str]]] = {
    "BULL": {
        "preferred": [],
        "allowed": ["technology", "semiconductors"],
        "avoid": [
            "consumer_discretionary", "communication", "financials", "industrials",
            "materials", "energy", "healthcare", "utilities", "consumer_staples", "real_estate",
        ],
    },
    "WEAK_BULL": {
        "preferred": ["technology", "semiconductors"],
        "allowed": ["consumer_discretionary", "communication", "financials"],
        "avoid": ["healthcare", "utilities", "consumer_staples", "real_estate"],
    },
    "CHOPPY": {
        "preferred": ["technology", "semiconductors", "consumer_discretionary"],
        "allowed": ["communication", "financials", "industrials", "materials", "energy"],
        "avoid": ["utilities", "consumer_staples"],
    },
    "BEAR": {
        "preferred": [
            "technology", "semiconductors", "consumer_discretionary", "communication",
            "financials", "industrials", "materials", "energy",
        ],
        "allowed": ["healthcare", "real_estate"],
        "avoid": ["utilities", "consumer_staples"],
    },
}


def _normalize_label(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(text or "").lower().strip()).strip("_")


def normalize_sector_name(sector: str) -> str:
    normalized = _normalize_label(sector)
    return SECTOR_NORMALIZATION_ALIASES.get(normalized, normalized)


@dataclass
class SectorFilterResult:
    symbol: str
    sector: str
    regime: str
    allowed: bool
    category: str
    reason: str


class SectorRegimeFilter:
    def __init__(
        self,
        custom_config: Optional[dict] = None,
        custom_short_config: Optional[dict] = None,
    ) -> None:
        self._long_config = custom_config or LONG_REGIME_SECTOR_CONFIG
        self._short_config = custom_short_config or SHORT_REGIME_SECTOR_CONFIG

    @staticmethod
    def _normalize(text: str) -> str:
        return _normalize_label(text)

    def _get_regime_cfg(self, regime: str, direction: str) -> dict[str, list[str]]:
        config = self._short_config if self._normalize(direction) == "short" else self._long_config
        regime_norm = self._normalize(regime)
        for key, value in config.items():
            if self._normalize(key) == regime_norm:
                return value
        log.warning("SectorRegimeFilter: unknown regime '%s', defaulting to CHOPPY", regime)
        return config.get("CHOPPY", {"preferred": [], "allowed": [], "avoid": []})

    def _category(self, sector: str, regime: str, direction: str) -> str:
        cfg = self._get_regime_cfg(regime, direction)
        sector_norm = normalize_sector_name(sector)
        for category in ("preferred", "allowed", "avoid"):
            if sector_norm in [normalize_sector_name(item) for item in cfg.get(category, [])]:
                return category
        return "allowed"

    def allow(self, sector: str, regime: str) -> bool:
        return self.allow_long(sector, regime)

    def allow_long(self, sector: str, regime: str) -> bool:
        return self._category(sector, regime, "long") != "avoid"

    def allow_short(self, sector: str, regime: str) -> bool:
        return self._category(sector, regime, "short") != "avoid"

    def category(self, sector: str, regime: str) -> str:
        return self.category_long(sector, regime)

    def category_long(self, sector: str, regime: str) -> str:
        return self._category(sector, regime, "long")

    def category_short(self, sector: str, regime: str) -> str:
        return self._category(sector, regime, "short")

    def sector_score_bonus(self, sector: str, regime: str) -> float:
        return self.sector_score_bonus_long(sector, regime)

    def sector_score_bonus_long(self, sector: str, regime: str) -> float:
        return {"preferred": 0.15, "allowed": 0.0, "avoid": -0.30}.get(
            self.category_long(sector, regime),
            0.0,
        )

    def sector_score_bonus_short(self, sector: str, regime: str) -> float:
        return {"preferred": 0.15, "allowed": 0.0, "avoid": -0.30}.get(
            self.category_short(sector, regime),
            0.0,
        )

    def rank_sectors(self, regime: str, direction: str = "long") -> list[str]:
        cfg = self._get_regime_cfg(regime, direction)
        return list(cfg.get("preferred", [])) + list(cfg.get("allowed", [])) + list(cfg.get("avoid", []))

    def filter_watchlist(
        self,
        watchlist: list[dict],
        regime: str,
        include_allowed: bool = True,
        direction: str = "long",
    ) -> list[SectorFilterResult]:
        results: list[SectorFilterResult] = []
        for item in watchlist:
            symbol = item.get("symbol", "")
            sector = item.get("sector", "")
            if not sector:
                results.append(
                    SectorFilterResult(
                        symbol=symbol,
                        sector="unknown",
                        regime=regime,
                        allowed=True,
                        category="allowed",
                        reason=f"unknown_sector_defaulted_to_allowed_{self._normalize(direction)}",
                    )
                )
                continue

            category = self._category(sector, regime, direction)
            allowed = category in ("preferred", "allowed") if include_allowed else category == "preferred"
            results.append(
                SectorFilterResult(
                    symbol=symbol,
                    sector=sector,
                    regime=regime,
                    allowed=allowed,
                    category=category,
                    reason=f"{self._normalize(direction)}_{self._normalize(regime)}_{category}",
                )
            )

        return [result for result in results if result.allowed]

    def get_preferred_etfs(self, regime: str, direction: str = "long") -> list[str]:
        cfg = self._get_regime_cfg(regime, direction)
        preferred = cfg.get("preferred", [])
        return [
            SECTOR_ETF_MAP[normalize_sector_name(item)]
            for item in preferred
            if normalize_sector_name(item) in SECTOR_ETF_MAP
        ]


REGIME_SECTOR_CONFIG = LONG_REGIME_SECTOR_CONFIG
