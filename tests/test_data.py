import unittest
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd

import db
import fetcher


class DataTests(unittest.TestCase):
    def test_current_session_is_excluded_and_dates_normalized(self):
        history = pd.DataFrame({"Close": [10, 11, 12]}, index=pd.date_range("2026-09-04", periods=3, tz="America/New_York"))
        result = fetcher._completed_history(history, today=date(2026, 9, 6))
        self.assertEqual(len(result), 2)
        self.assertIsNone(result.index.tz)
        self.assertEqual(result.index[-1].date(), date(2026, 9, 5))

    def test_relative_returns_use_exact_endpoints_and_full_windows(self):
        index = pd.bdate_range("2025-01-01", periods=127)
        stock = pd.DataFrame({"Close": np.arange(100, 227)}, index=index)
        benchmark = pd.DataFrame({"Close": np.arange(200, 327)}, index=index)
        returns = fetcher._relative_returns(stock, benchmark)
        self.assertAlmostEqual(returns["relative_return_6m"], 226 / 100 - 326 / 200)
        self.assertAlmostEqual(returns["relative_return_1m"], 226 / 205 - 326 / 305)
        missing = fetcher._relative_returns(stock, benchmark.drop(index[-1]))
        self.assertTrue(all(value is None for value in missing.values()))
        short = fetcher._relative_returns(stock.tail(21), benchmark)
        self.assertTrue(all(value is None for value in short.values()))
        missing_stock_session = fetcher._relative_returns(stock.drop(index[-10]), benchmark)
        self.assertTrue(all(value is None for value in missing_stock_session.values()))
        benchmark.loc[index[-10], "Close"] = float("nan")
        gaps = fetcher._relative_returns(stock, benchmark)
        self.assertTrue(all(value is None for value in gaps.values()))

    def test_batch_loads_benchmark_once(self):
        benchmark = pd.DataFrame()
        with patch.object(db, "get_stale_tickers", return_value=["AAA", "BBB"]), patch.object(db, "upsert_fundamentals", return_value=2), patch.object(fetcher, "_load_benchmark_history", return_value=benchmark) as load, patch.object(fetcher, "fetch_fundamentals", return_value={}) as fetch, patch.object(fetcher.settings, "RATE_LIMIT_PER_SEC", 0):
            self.assertEqual(fetcher.fetch_and_persist_stale(show_progress=False), 2)
        load.assert_called_once()
        self.assertEqual(fetch.call_count, 2)
        self.assertTrue(all(call.kwargs["benchmark_history"] is benchmark for call in fetch.call_args_list))

    def test_fetch_uses_completed_close_instead_of_live_quote(self):
        history = pd.DataFrame({"Close": [10.0, 11.0], "Volume": [100, 200]}, index=pd.to_datetime(["2025-01-02", "2025-01-03"]))
        stock = MagicMock()
        stock.info = {"currentPrice": 999, "averageVolume": 99999}
        stock.history.return_value = history
        with patch.object(fetcher.yf, "Ticker", return_value=stock):
            record = fetcher.fetch_fundamentals("AAA", benchmark_history=pd.DataFrame())
        self.assertEqual(record["current_price"], 11)
        self.assertEqual(record["volume"], 150)
        self.assertEqual(record["price_as_of"], date(2025, 1, 3))
        self.assertFalse(record["technical_valid"])
        self.assertIsNone(record["pattern_score"])

        stock.history.return_value.loc[pd.Timestamp("2025-01-03"), "Close"] = float("nan")
        with patch.object(fetcher.yf, "Ticker", return_value=stock):
            invalid = fetcher.fetch_fundamentals("AAA", benchmark_history=pd.DataFrame())
        self.assertIsNone(invalid["current_price"])
        self.assertIsNone(invalid["price_as_of"])

    def test_staleness_retries_missing_and_invalid_prices(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = [{"ticker": "AAA"}]
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        @contextmanager
        def connection():
            yield conn
        with patch.object(db, "get_connection", connection):
            self.assertEqual(db.get_stale_tickers(), ["AAA"])
        sql, params = cursor.execute.call_args.args
        self.assertIn("f.price_as_of IS NULL", sql)
        self.assertIn("f.technical_valid IS NOT TRUE", sql)
        self.assertLess(params["latest_session"].weekday(), 5)
        self.assertEqual(params["max_age_days"], db.settings.DATA_MAX_AGE_DAYS)

    def test_query_unlimited_and_ev_filter(self):
        cursor = MagicMock()
        cursor.fetchall.return_value = []
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        @contextmanager
        def connection():
            yield conn
        with patch.object(db, "get_connection", connection):
            db.query_screened_stocks(max_ev_ebitda=18)
        sql, params = cursor.execute.call_args.args
        self.assertIsNone(params["limit"])
        self.assertEqual(params["max_ev_ebitda"], 18)
        self.assertIn("lf.ev_to_ebitda > 0", sql)
        self.assertIn("Financial Services", sql)
        self.assertIn("lf.price_as_of", sql)
        self.assertIn("lf.updated_at", sql)

    def test_upsert_preserves_unknown_technicals(self):
        cursor = MagicMock()
        conn = MagicMock()
        conn.cursor.return_value.__enter__.return_value = cursor
        @contextmanager
        def connection():
            yield conn
        with patch.object(db, "get_connection", connection):
            db.upsert_fundamentals([{"ticker": "AAA", "fiscal_date": date(2026, 9, 1)}])
        sql, records = cursor.executemany.call_args.args
        self.assertIsNone(records[0]["pattern_score"])
        self.assertIsNone(records[0]["technical_valid"])
        self.assertIsNone(records[0]["price_as_of"])
        schema = Path(db.__file__).with_name("schema.sql").read_text()
        for field in ("pattern_score", "technical_valid", "is_stage_2", "is_breakout", "price_as_of", "relative_return_6m"):
            self.assertIn(f"ADD COLUMN IF NOT EXISTS {field}", schema)
            self.assertIn(f"%({field})s", sql)


if __name__ == "__main__":
    unittest.main()
