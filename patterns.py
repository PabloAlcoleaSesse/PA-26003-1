"""
Technical Pattern Recognition and Price Action Analysis Engine.

Detects high-probability institutional breakout patterns, including:
  - Mark Minervini Stage 2 Trend Template (U.S. Investing Championship setup)
  - Volatility Contraction Pattern (VCP) / Coiling Consolidation
  - 20-Day / 50-Day High Resistance Breakout with Volume Confirmation
  - Institutional Accumulation (Up/Down Volume Ratio >= 1.2x)
  - Bullish Relative Strength Index (RSI 14) Momentum
  - Golden Cross (SMA 50 crossing above SMA 200)
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def analyze_technical_patterns(hist: pd.DataFrame) -> dict[str, Any]:
    """
    Analyze daily historical OHLCV data to detect technical patterns and score setups.

    Args:
        hist: Pandas DataFrame containing ['Open', 'High', 'Low', 'Close', 'Volume']
              with at least 252 complete trading sessions of data.

    Returns:
        Dict containing pattern metrics and detected signal labels.
    """
    default_result = {
        "pattern_score": None,
        "technical_valid": False,
        "detected_patterns": "None",
        "dist_52w_high": None,
        "rsi_14": None,
        "ud_volume_ratio": None,
        "is_stage_2": False,
        "stage_2_rules_passed": 0,
        "is_vcp": False,
        "is_breakout": False,
        "price_return_1m": None,
        "price_return_3m": None,
        "price_return_6m": None,
        "sma_50": None,
        "sma_200": None,
    }

    if hist is None or hist.empty or len(hist) < 252:
        return default_result

    try:
        # Validate aligned sessions together; dropping columns independently can
        # pair one day's price move with another day's volume.
        data = hist.loc[:, ["Open", "High", "Low", "Close", "Volume"]].tail(252)
        data = data.apply(pd.to_numeric, errors="coerce")
        if (
            not data.index.is_monotonic_increasing
            or data.index.has_duplicates
            or not np.isfinite(data.to_numpy()).all()
            or (data[["Open", "High", "Low", "Close"]] <= 0).any().any()
            or (data["Volume"] < 0).any()
            or (data["High"] < data[["Open", "Low", "Close"]].max(axis=1)).any()
            or (data["Low"] > data[["Open", "High", "Close"]].min(axis=1)).any()
        ):
            return default_result
        close, high, low, volume = (data[column] for column in ["Close", "High", "Low", "Volume"])

        current_price = float(close.iloc[-1])
        current_vol = float(volume.iloc[-1])
        high_52w = float(high.max())
        low_52w = float(low.min())

        dist_52w_high = (current_price - high_52w) / high_52w if high_52w > 0 else 0.0
        dist_52w_low = (current_price - low_52w) / low_52w if low_52w > 0 else 0.0

        # Moving Averages
        sma50 = close.rolling(50).mean()
        sma150 = close.rolling(150).mean()
        sma200 = close.rolling(200).mean()

        sma50_curr = float(sma50.iloc[-1])
        sma150_curr = float(sma150.iloc[-1])
        sma200_curr = float(sma200.iloc[-1])
        vol_50d_prior = float(volume.iloc[-51:-1].mean())

        # -------------------------------------------------------------------------
        # 1. Minervini Stage 2 Trend Template Rules
        # -------------------------------------------------------------------------
        cond_above_200 = current_price > sma200_curr
        cond_above_150 = current_price > sma150_curr
        cond_above_50 = current_price > sma50_curr
        cond_order = sma50_curr > sma150_curr > sma200_curr

        # 200 SMA trending up over the past ~2 months (42 sessions).
        # A longer window catches stocks entering Stage 2 as the 200 SMA
        # flattens and begins to turn after a correction.
        sma200_up = float(sma200.iloc[-1]) >= float(sma200.iloc[-42])

        cond_above_low = dist_52w_low >= 0.20       # At least 20% off 52W low
        cond_near_high = dist_52w_high >= -0.22     # Within 22% of 52W high

        stage_2_rules = [cond_above_200, cond_above_150, cond_above_50,
                         cond_order, sma200_up, cond_above_low, cond_near_high]
        stage_2_rules_passed = sum(stage_2_rules)
        stage_2_trend = bool(stage_2_rules_passed == 7)

        # -------------------------------------------------------------------------
        # 2. Volatility Contraction Pattern (VCP)
        # Price swings contract tightly near highs with lower volume on pullbacks
        # -------------------------------------------------------------------------
        recent_range = (high.iloc[-10:].max() - low.iloc[-10:].min()) / current_price
        past_range = (high.iloc[-45:-10].max() - low.iloc[-45:-10].min()) / current_price
        vcp_tightness = (recent_range / past_range) if past_range > 0 else 1.0

        is_vcp = bool(
            stage_2_trend
            and past_range >= 0.04
            and recent_range <= 0.08
            and vcp_tightness < 0.48
            and dist_52w_high >= -0.16
            and volume.iloc[-10:].mean() < 0.8 * volume.iloc[-45:-10].mean()
        )

        # -------------------------------------------------------------------------
        # 3. Consolidation Breakout with Volume Confirmation
        #    Dual-timeframe: short (25d) with high-conviction volume (1.40x),
        #    and longer (50d) for classical bases with standard volume (1.20x).
        # -------------------------------------------------------------------------
        prev_resistance_short = float(high.iloc[-26:-1].max())
        prev_resistance_long = float(high.iloc[-51:-1].max())
        is_short_breakout = bool(
            current_price > prev_resistance_short
            and vol_50d_prior > 0
            and current_vol >= (1.40 * vol_50d_prior)
            and dist_52w_high >= -0.15
        )
        is_long_breakout = bool(
            current_price > prev_resistance_long
            and vol_50d_prior > 0
            and current_vol >= (1.20 * vol_50d_prior)
            and dist_52w_high >= -0.15
        )
        is_breakout = is_short_breakout or is_long_breakout

        # -------------------------------------------------------------------------
        # 4. Institutional Accumulation / Distribution (Up/Down Volume)
        # -------------------------------------------------------------------------
        delta = close.diff()
        recent_delta = delta.iloc[-20:]
        recent_volume = volume.iloc[-20:]
        up_volume = recent_volume[recent_delta > 0].sum()
        down_volume = recent_volume[recent_delta < 0].sum()
        ud_volume_ratio = float(up_volume / down_volume) if down_volume > 0 else None
        is_accumulation = bool(up_volume > 0 and (down_volume == 0 or ud_volume_ratio >= 1.20))

        # -------------------------------------------------------------------------
        # 5. Relative Strength Index (RSI 14)
        # -------------------------------------------------------------------------
        gain = float(delta.clip(lower=0).iloc[-14:].mean())
        loss = float((-delta.clip(upper=0)).iloc[-14:].mean())
        rsi_14 = (100.0 if gain > 0 else 50.0) if loss == 0 else 100.0 - 100.0 / (1.0 + gain / loss)

        # Bullish momentum sweet spot: between 50 and 70 (not dead, not hyper-stretched)
        is_bullish_rsi = bool(50.0 < rsi_14 <= 72.0)

        # -------------------------------------------------------------------------
        # 6. Synthesize Signals & Compute Pattern Score (0 - 100)
        # -------------------------------------------------------------------------
        patterns: list[str] = []
        score = 30.0  # Base line

        if stage_2_trend:
            patterns.append("Stage 2")
            score += 30.0
        elif stage_2_rules_passed >= 5:
            patterns.append("Stage 2 Emerging")
            score += 15.0
        elif cond_above_50 and cond_above_200:
            score += 15.0

        if is_vcp:
            patterns.append("VCP Coiling")
            score += 20.0

        if is_breakout:
            patterns.append("Breakout")
            score += 20.0

        if is_accumulation:
            patterns.append("Accumulation")
            score += 12.0

        if is_bullish_rsi:
            patterns.append("Momentum")
            score += 8.0

        if dist_52w_high >= -0.08:
            score += 10.0  # Leader proximity bonus

        pattern_score = min(100.0, max(10.0, score))
        pattern_str = ", ".join(patterns) if patterns else "Neutral"

        return {
            "pattern_score": round(pattern_score, 1),
            "technical_valid": True,
            "detected_patterns": pattern_str,
            "dist_52w_high": round(dist_52w_high, 4),
            "rsi_14": round(rsi_14, 1),
            "ud_volume_ratio": round(ud_volume_ratio, 2) if ud_volume_ratio is not None else None,
            "is_stage_2": stage_2_trend,
            "stage_2_rules_passed": stage_2_rules_passed,
            "is_vcp": is_vcp,
            "is_breakout": is_breakout,
            "price_return_1m": float(current_price / close.iloc[-22] - 1),
            "price_return_3m": float(current_price / close.iloc[-64] - 1),
            "price_return_6m": float(current_price / close.iloc[-127] - 1),
            "sma_50": sma50_curr,
            "sma_200": sma200_curr,
        }

    except Exception as exc:
        logger.debug("Error computing technical patterns: %s", exc)
        return default_result
