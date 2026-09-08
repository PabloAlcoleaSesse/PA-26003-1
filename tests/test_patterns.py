import unittest

import numpy as np
import pandas as pd

from patterns import analyze_technical_patterns


def history(prices, volume=1000.0):
    prices = np.asarray(prices, dtype=float)
    return pd.DataFrame(
        {"Open": prices, "High": prices * 1.002, "Low": prices * 0.998,
         "Close": prices, "Volume": volume},
        index=pd.bdate_range("2024-01-01", periods=len(prices)),
    )


class TechnicalPatternsTests(unittest.TestCase):
    def test_insufficient_or_invalid_history_is_unavailable(self):
        malformed = history(np.linspace(50, 100, 252))
        malformed.iloc[-10, malformed.columns.get_loc("Volume")] = np.nan
        for data in (None, history([100] * 251), malformed,
                     malformed.drop(columns="High")):
            with self.subTest(data_type=type(data)):
                result = analyze_technical_patterns(data)
                self.assertFalse(result["technical_valid"])
                self.assertIsNone(result["pattern_score"])
                for flag in ("is_stage_2", "is_vcp", "is_breakout"):
                    self.assertFalse(result[flag])

    def test_flat_stock_has_no_momentum_or_setup(self):
        result = analyze_technical_patterns(history([100] * 252))
        self.assertTrue(result["technical_valid"])
        self.assertEqual(result["rsi_14"], 50)
        self.assertNotIn("Momentum", result["detected_patterns"])
        self.assertNotIn("Accumulation", result["detected_patterns"])
        self.assertFalse(result["is_stage_2"])
        self.assertFalse(result["is_vcp"])
        self.assertFalse(result["is_breakout"])
        self.assertEqual(result["price_return_6m"], 0)

    def test_downtrend_is_not_stage_2_or_vcp(self):
        result = analyze_technical_patterns(history(np.linspace(120, 60, 252)))
        self.assertFalse(result["is_stage_2"])
        self.assertFalse(result["is_vcp"])
        self.assertEqual(result["rsi_14"], 0)
        self.assertLess(result["price_return_6m"], 0)

    def test_uptrend_and_returns_use_full_intervals(self):
        prices = np.linspace(50, 100, 252)
        result = analyze_technical_patterns(history(prices))
        self.assertTrue(result["is_stage_2"])
        self.assertEqual(result["rsi_14"], 100)
        self.assertAlmostEqual(result["sma_200"], prices[-200:].mean())
        self.assertAlmostEqual(result["sma_50"], prices[-50:].mean())
        for name, sessions in (("1m", 21), ("3m", 63), ("6m", 126)):
            self.assertAlmostEqual(result[f"price_return_{name}"], prices[-1] / prices[-sessions - 1] - 1)

    def test_breakout_exceeds_resistance_and_excludes_today_from_volume(self):
        data = history(np.linspace(50, 100, 252))
        resistance = data["High"].iloc[-26:-1].max()
        data.iloc[-1, data.columns.get_loc("Volume")] = 1200
        for price, expected in ((resistance * 0.998, False), (resistance, False), (resistance * 1.01, True)):
            with self.subTest(price=price):
                data.loc[data.index[-1], ["Open", "Close", "High", "Low"]] = [price, price, price * 1.002, price * 0.998]
                self.assertEqual(analyze_technical_patterns(data)["is_breakout"], expected)

    def test_accumulation_uses_one_shared_twenty_session_window(self):
        prices = np.concatenate((np.linspace(40, 100, 232), np.linspace(99, 80, 20)))
        data = history(prices)
        data.loc[data.index[:-20], "Volume"] = 100000
        result = analyze_technical_patterns(data)
        self.assertEqual(result["ud_volume_ratio"], 0)
        self.assertNotIn("Accumulation", result["detected_patterns"])

    def test_vcp_requires_volume_contraction_in_uptrend(self):
        prices = np.concatenate((np.linspace(50, 100, 207),
                                 100 + 4 * np.sin(np.linspace(0, 4 * np.pi, 35)),
                                 np.linspace(102, 102.5, 10)))
        data = history(prices)
        self.assertFalse(analyze_technical_patterns(data)["is_vcp"])
        data.loc[data.index[-10:], "Volume"] = 500
        result = analyze_technical_patterns(data)
        self.assertTrue(result["is_stage_2"])
        self.assertTrue(result["is_vcp"])
        data.loc[data.index[:207], ["Open", "High", "Low", "Close"]] *= 3
        result = analyze_technical_patterns(data)
        self.assertFalse(result["is_stage_2"])
        self.assertFalse(result["is_vcp"])


if __name__ == "__main__":
    unittest.main()
