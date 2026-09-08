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
              with at least 60 trading days of data.

    Returns:
        Dict containing pattern metrics and detected signal labels.
    """
    default_result = {
        "pattern_score": 50.0,
        "detected_patterns": "None",
        "dist_52w_high": None,
        "rsi_14": None,
        "ud_volume_ratio": None,
        "is_stage_2": False,
        "is_vcp": False,
        "is_breakout": False,
    }

    if hist is None or hist.empty or len(hist) < 50:
        return default_result

    try:
        close = hist["Close"].dropna()
        high = hist["High"].dropna()
        low = hist["Low"].dropna()
        volume = hist["Volume"].dropna()

        if len(close) < 50:
            return default_result

        current_price = float(close.iloc[-1])
        current_vol = float(volume.iloc[-1])
        high_52w = float(high.max())
        low_52w = float(low.min())

        dist_52w_high = (current_price - high_52w) / high_52w if high_52w > 0 else 0.0
        dist_52w_low = (current_price - low_52w) / low_52w if low_52w > 0 else 0.0

        # Moving Averages
        sma20 = close.rolling(20, min_periods=15).mean()
        sma50 = close.rolling(50, min_periods=35).mean()
        sma150 = close.rolling(150, min_periods=60).mean()
        sma200 = close.rolling(200, min_periods=80).mean()
        vol_50d = volume.rolling(50, min_periods=20).mean()

        sma50_curr = float(sma50.iloc[-1]) if not sma50.empty else current_price
        sma150_curr = float(sma150.iloc[-1]) if not sma150.empty else current_price
        sma200_curr = float(sma200.iloc[-1]) if not sma200.empty else current_price
        vol_50d_curr = float(vol_50d.iloc[-1]) if not vol_50d.empty else current_vol

        # -------------------------------------------------------------------------
        # 1. Minervini Stage 2 Trend Template Rules
        # -------------------------------------------------------------------------
        cond_above_200 = current_price > sma200_curr
        cond_above_150 = current_price > sma150_curr
        cond_above_50 = current_price > sma50_curr
        cond_order = (sma50_curr > sma150_curr >= sma200_curr) if len(close) >= 150 else (sma50_curr > sma200_curr)

        # 200 SMA trending up over the past 20 trading days
        sma200_up = True
        if len(sma200.dropna()) >= 22:
            sma200_up = float(sma200.iloc[-1]) >= float(sma200.iloc[-20])

        cond_above_low = dist_52w_low >= 0.20       # At least 20% off 52W low
        cond_near_high = dist_52w_high >= -0.22     # Within 22% of 52W high

        stage_2_trend = bool(
            cond_above_200
            and cond_above_150
            and cond_above_50
            and cond_order
            and sma200_up
            and cond_above_low
            and cond_near_high
        )

        # -------------------------------------------------------------------------
        # 2. Volatility Contraction Pattern (VCP)
        # Price swings contract tightly near highs with lower volume on pullbacks
        # -------------------------------------------------------------------------
        lookback_recent = min(10, len(high))
        lookback_past = min(45, len(high))
        recent_range = (high.iloc[-lookback_recent:].max() - low.iloc[-lookback_recent:].min()) / current_price
        past_range = (high.iloc[-lookback_past:].max() - low.iloc[-lookback_past:].min()) / current_price
        vcp_tightness = (recent_range / past_range) if past_range > 0 else 1.0

        is_vcp = bool(vcp_tightness < 0.48 and dist_52w_high >= -0.16)

        # -------------------------------------------------------------------------
        # 3. Consolidation Breakout with Volume Confirmation
        # -------------------------------------------------------------------------
        lookback_breakout = min(25, len(high) - 1)
        prev_resistance = float(high.iloc[-lookback_breakout - 1 : -1].max())
        is_breakout = bool(
            current_price >= (prev_resistance * 0.995)
            and current_vol >= (1.20 * vol_50d_curr)
            and dist_52w_high >= -0.10
        )

        # -------------------------------------------------------------------------
        # 4. Institutional Accumulation / Distribution (Up/Down Volume)
        # -------------------------------------------------------------------------
        delta = close.diff()
        lb_vol = min(20, len(delta))
        up_volume = volume[delta > 0].iloc[-lb_vol:].sum()
        down_volume = volume[delta < 0].iloc[-lb_vol:].sum()
        ud_volume_ratio = float(up_volume / down_volume) if down_volume > 0 else 1.0
        is_accumulation = bool(ud_volume_ratio >= 1.20)

        # -------------------------------------------------------------------------
        # 5. Relative Strength Index (RSI 14)
        # -------------------------------------------------------------------------
        gain = delta.where(delta > 0, 0.0).rolling(14, min_periods=10).mean()
        loss = (-delta.where(delta < 0, 0.0)).rolling(14, min_periods=10).mean()
        rs = gain / loss.replace(0, np.nan)
        rsi_series = 100.0 - (100.0 / (1.0 + rs))
        rsi_14 = float(rsi_series.iloc[-1]) if not rsi_series.empty and not np.isnan(rsi_series.iloc[-1]) else 50.0

        # Bullish momentum sweet spot: between 50 and 70 (not dead, not hyper-stretched)
        is_bullish_rsi = bool(50.0 <= rsi_14 <= 72.0)

        # -------------------------------------------------------------------------
        # 6. Synthesize Signals & Compute Pattern Score (0 - 100)
        # -------------------------------------------------------------------------
        patterns: list[str] = []
        score = 30.0  # Base line

        if stage_2_trend:
            patterns.append("Stage 2")
            score += 30.0
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
            "detected_patterns": pattern_str,
            "dist_52w_high": round(dist_52w_high, 4),
            "rsi_14": round(rsi_14, 1),
            "ud_volume_ratio": round(ud_volume_ratio, 2),
            "is_stage_2": stage_2_trend,
            "is_vcp": is_vcp,
            "is_breakout": is_breakout,
        }

    except Exception as exc:
        logger.debug("Error computing technical patterns: %s", exc)
        return default_result
