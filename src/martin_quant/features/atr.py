from __future__ import annotations

import pandas as pd


def compute_true_range(
    df: pd.DataFrame,
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close",
) -> pd.Series:
    for col in (high_col, low_col, close_col):
        if col not in df.columns:
            raise KeyError(f"Missing column: {col}")

    prev_close = df[close_col].shift(1)

    tr_1 = df[high_col] - df[low_col]
    tr_2 = (df[high_col] - prev_close).abs()
    tr_3 = (df[low_col] - prev_close).abs()

    tr = pd.concat([tr_1, tr_2, tr_3], axis=1).max(axis=1)
    if not tr.empty:
        tr.iloc[0] = pd.NA
    return tr.rename("true_range")


def compute_atr(
    df: pd.DataFrame,
    period: int = 14,
    high_col: str = "high",
    low_col: str = "low",
    close_col: str = "close",
) -> pd.Series:
    if period <= 0:
        raise ValueError("period must be > 0")

    tr = compute_true_range(
        df=df,
        high_col=high_col,
        low_col=low_col,
        close_col=close_col,
    )
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean().rename(f"atr_{period}")


def compute_adr(
    df: pd.DataFrame,
    period: int = 20,
    high_col: str = "high",
    low_col: str = "low",
) -> pd.Series:
    if period <= 0:
        raise ValueError("period must be > 0")

    for col in (high_col, low_col):
        if col not in df.columns:
            raise KeyError(f"Missing column: {col}")

    daily_range = (df[high_col] - df[low_col]).rename("daily_range")
    return daily_range.rolling(period, min_periods=period).mean().rename(f"adr_{period}")


def add_atr_features(
    df: pd.DataFrame,
    atr_periods: tuple[int, ...] = (14,),
    adr_periods: tuple[int, ...] = (20,),
    close_col: str = "close",
    high_col: str = "high",
    low_col: str = "low",
) -> pd.DataFrame:
    out = df.copy()

    for period in atr_periods:
        atr = compute_atr(
            df=out,
            period=period,
            high_col=high_col,
            low_col=low_col,
            close_col=close_col,
        )
        out[atr.name] = atr
        out[f"{atr.name}_pct"] = (out[atr.name] / out[close_col]) * 100.0

    for period in adr_periods:
        adr = compute_adr(
            df=out,
            period=period,
            high_col=high_col,
            low_col=low_col,
        )
        out[adr.name] = adr
        out[f"{adr.name}_pct"] = (out[adr.name] / out[close_col]) * 100.0

    return out
