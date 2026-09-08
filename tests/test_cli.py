import contextlib
import io
import unittest
from unittest.mock import patch

import main


class CliTests(unittest.TestCase):
    def invoke(self, arguments):
        with patch.object(main.db, "close_pool") as close, contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as result:
                main.main(arguments)
        self.assertEqual(result.exception.code, 0)
        close.assert_called_once_with()

    def test_screen_propagates_eligibility_and_audit_flags(self):
        with patch.object(main.screener, "run_screen", return_value=[]) as screen:
            self.invoke(["screen", "--strategy", "minervini_trend", "--universe", "sp500",
                         "--include-watchlist", "--max-price-age-days", "2",
                         "--audit-dir", "test-runs", "--top", "7", "--output-csv", "test.csv"])
        arguments = screen.call_args.kwargs
        self.assertEqual(arguments["strategy"], "minervini_trend")
        self.assertEqual(arguments["universe"], "sp500")
        self.assertTrue(arguments["include_watchlist"])
        self.assertEqual(arguments["max_price_age_days"], 2)
        self.assertEqual(arguments["audit_dir"], "test-runs")
        self.assertEqual(arguments["limit"], 7)
        self.assertEqual(arguments["output_csv"], "test.csv")

    def run_pipeline(self, arguments):
        companies = [{"ticker": "TEST"}]
        with patch.object(main.db, "init_db") as initialize, \
             patch.object(main.universe, "fetch_universe", return_value=companies) as universe, \
             patch.object(main.db, "upsert_companies") as upsert, \
             patch.object(main.fetcher, "fetch_and_persist_stale", return_value=1) as fetch, \
             patch.object(main.screener, "run_screen", return_value=[]) as screen:
            self.invoke(["run-all", *arguments])
        initialize.assert_called_once_with()
        upsert.assert_called_once_with(companies)
        self.assertEqual(universe.call_args.kwargs["source"], fetch.call_args.kwargs["universe"])
        return fetch.call_args.kwargs, screen.call_args.kwargs

    def test_run_all_refreshes_full_universe_with_daily_default(self):
        with patch.object(main.settings, "DATA_MAX_AGE_DAYS", 1):
            fetch, screen = self.run_pipeline([])
        self.assertIsNone(fetch["limit"])
        self.assertEqual(fetch["max_age_days"], 1)
        self.assertFalse(screen["include_watchlist"])
        self.assertEqual(screen["max_price_age_days"], 5)
        self.assertEqual(screen["audit_dir"], "runs")

    def test_run_all_propagates_refresh_and_screen_flags(self):
        fetch, screen = self.run_pipeline([
            "--universe", "sp400", "--strategy", "high_growth_momentum",
            "--limit", "40", "--workers", "3", "--max-age-days", "0",
            "--include-watchlist", "--max-price-age-days", "4", "--audit-dir", "test-runs",
            "--top", "8", "--output-csv", "test.csv",
        ])
        self.assertEqual(fetch["limit"], 40)
        self.assertEqual(fetch["max_age_days"], 0)
        self.assertEqual(fetch["max_workers"], 3)
        self.assertEqual(screen["strategy"], "high_growth_momentum")
        self.assertEqual(screen["universe"], "sp400")
        self.assertEqual(screen["limit"], 8)
        self.assertTrue(screen["include_watchlist"])
        self.assertEqual(screen["max_price_age_days"], 4)
        self.assertEqual(screen["audit_dir"], "test-runs")
        self.assertEqual(screen["output_csv"], "test.csv")

    def test_evaluate_routes_run_path_output_and_cost(self):
        with patch.object(main.outcomes, "evaluate_run", return_value=[{"status": "complete"}]) as evaluate:
            self.invoke(["evaluate", "--run", "runs/example.json", "--output-csv", "evaluation.csv",
                         "--cost-bps", "15"])
        evaluate.assert_called_once_with("runs/example.json", "evaluation.csv", cost_bps=15.0)


if __name__ == "__main__":
    unittest.main()
