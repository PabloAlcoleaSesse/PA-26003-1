import unittest
from datetime import date

import pandas as pd

from outcomes import measure_outcomes


class OutcomeTests(unittest.TestCase):
    def bars(self, count=60):
        return pd.DataFrame({"Open": [100.0] * count, "Close": [110.0] * count},
                            index=pd.bdate_range("2026-01-05", periods=count))

    def test_forward_entry_costs_and_excess(self):
        stock = self.bars()
        # Signal day's large price must never be used as entry or return input.
        stock.loc[pd.Timestamp("2026-01-02")] = [1.0, 1.0]
        benchmark = self.bars()
        benchmark["Close"] = 105.0
        rows = measure_outcomes(stock, benchmark, date(2026, 1, 2), cost_bps=10)
        self.assertEqual([r["status"] for r in rows], ["complete"] * 3)
        self.assertAlmostEqual(rows[0]["net_return"], .099)
        self.assertAlmostEqual(rows[0]["excess_return"], .05)
        self.assertEqual(rows[0]["entry_date"], date(2026, 1, 5))

    def test_pending_and_missing_sessions(self):
        stock, benchmark = self.bars(10), self.bars(10)
        stock = stock.drop(stock.index[2])
        rows = measure_outcomes(stock, benchmark, date(2026, 1, 2))
        self.assertEqual(rows[0]["status"], "price_data_unavailable")
        self.assertEqual(rows[1]["status"], "pending")

    def test_drawdown_includes_entry_and_peaks(self):
        stock = self.bars(5)
        stock["Close"] = [90.0, 120.0, 96.0, 100.0, 110.0]
        row = measure_outcomes(stock, self.bars(5), date(2026, 1, 2))[0]
        self.assertAlmostEqual(row["max_drawdown"], -.2)

    def test_missing_benchmark_is_not_success(self):
        rows = measure_outcomes(self.bars(), pd.DataFrame(), date(2026, 1, 2))
        self.assertEqual(rows[0]["status"], "benchmark_unavailable")

    def test_invalid_cost(self):
        with self.assertRaises(ValueError):
            measure_outcomes(self.bars(), self.bars(), date(2026, 1, 2), cost_bps=-1)


if __name__ == "__main__":
    unittest.main()
