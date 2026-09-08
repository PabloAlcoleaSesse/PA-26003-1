import csv
import json
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

import screener


class ScreeningTests(unittest.TestCase):
    def candidate(self, **changes):
        row = dict(ticker="TEST", current_price=50, price_as_of=date(2026, 9, 8),
                   technical_valid=True, is_stage_2=True, is_vcp=True,
                   is_breakout=False, pattern_score=80, price_return_1m=0,
                   price_return_3m=.10, price_return_6m=.20, relative_return_3m=.05,
                   roe=.25, revenue_growth=.30, pe_forward=15, operating_margin=.25)
        return row | changes

    def screen(self, rows, **kwargs):
        with patch.object(screener.db, "query_screened_stocks", return_value=rows) as query:
            result = screener.run_screen(strategy="upcoming_breakouts", print_table=False,
                                         output_csv=None, audit_dir=None,
                                         as_of=date(2026, 9, 8), **kwargs)
        self.assertIsNone(query.call_args.kwargs["limit"])
        self.assertIsNone(query.call_args.kwargs["min_pattern_score"])
        return result

    def test_declining_futu_rejected_even_with_strong_fundamentals(self):
        self.assertEqual(self.screen([self.candidate(ticker="FUTU", price_return_6m=-.208)]), [])

    def test_flat_recent_consolidation_allowed(self):
        result = self.screen([self.candidate()])
        self.assertEqual(result[0]["setup_status"], "Setup forming")
        self.assertTrue(result[0]["eligible"])
        self.assertEqual(result[0]["eligibility_reasons"], [])

    def test_missing_stale_future_and_failed_technicals_rejected(self):
        for change in [dict(price_as_of=None), dict(price_as_of="2026-09-01"),
                       dict(price_as_of="2026-09-09"), dict(technical_valid=False),
                       dict(price_return_3m=None), dict(relative_return_3m=None),
                       dict(relative_return_3m=-.04), dict(current_price=float("nan"))]:
            with self.subTest(change=change):
                self.assertEqual(self.screen([self.candidate(**change)]), [])

    def test_zero_pattern_is_not_neutral(self):
        self.assertEqual(screener.calculate_composite_score({"pattern_score": 0})["pattern"], 0)
        self.assertEqual(self.screen([self.candidate(pattern_score=0)]), [])

    def test_winner_beyond_old_200_candidate_cutoff(self):
        rows = [self.candidate(ticker=f"BAD{i}", price_return_6m=-.1) for i in range(220)]
        rows.append(self.candidate(ticker="WINNER"))
        self.assertEqual(self.screen(rows, limit=1)[0]["ticker"], "WINNER")

    def test_watchlist_is_separate_and_does_not_displace_eligible(self):
        result = self.screen([self.candidate(ticker="BAD", price_return_6m=-.2),
                              self.candidate(ticker="GOOD")], include_watchlist=True)
        self.assertEqual([r["ticker"] for r in result], ["GOOD", "BAD"])
        self.assertEqual(result[1]["setup_status"], "Watchlist")
        self.assertIn("nonpositive_return_6m", result[1]["eligibility_reasons"])

    def test_alias_same_score(self):
        row = self.candidate()
        self.assertEqual(screener.calculate_composite_score(row, "breakout"),
                         screener.calculate_composite_score(row, "upcoming_breakouts"))

    def test_empty_csv_and_immutable_audits_capture_inputs(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "results.csv"
            audit = Path(directory) / "runs"
            with patch.object(screener.db, "query_screened_stocks", return_value=[
                self.candidate(ticker="FUTU", price_return_6m=Decimal("-0.208"))
            ]):
                for _ in range(2):
                    screener.run_screen(strategy="upcoming_breakouts", print_table=False,
                                        as_of=date(2026, 9, 8), output_csv=output, audit_dir=audit)
            with output.open() as handle:
                reader = csv.DictReader(handle)
                self.assertIn("setup_status", reader.fieldnames)
                self.assertEqual(list(reader), [])
            manifests = list(audit.glob("*.json"))
            self.assertEqual(len(manifests), 2)
            manifest = json.loads(manifests[0].read_text())
            self.assertEqual(manifest["selected"], [])
            self.assertEqual(manifest["watchlist"][0]["ticker"], "FUTU")
            self.assertEqual(manifest["rejection_counts"]["nonpositive_return_6m"], 1)
            with output.with_name("results.watchlist.csv").open() as handle:
                watchlist = list(csv.DictReader(handle))
            self.assertEqual(watchlist[0]["ticker"], "FUTU")
            self.assertEqual(watchlist[0]["eligible"], "False")
            self.assertEqual(manifest["candidates"][0]["inputs"]["price_return_6m"], -.208)
            self.assertIn("nonpositive_return_6m", manifest["candidates"][0]["eligibility_reasons"])

    def test_watchlist_export_clears_old_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "screen.csv"
            with patch.object(screener.db, "query_screened_stocks", return_value=[
                self.candidate(price_return_6m=-.2)
            ]) as query:
                screener.run_screen(strategy="upcoming_breakouts", print_table=False,
                                    as_of=date(2026, 9, 8), output_csv=output, audit_dir=None)
                query.return_value = [self.candidate()]
                screener.run_screen(strategy="upcoming_breakouts", print_table=False,
                                    as_of=date(2026, 9, 8), output_csv=output, audit_dir=None)
            with output.with_name("screen.watchlist.csv").open() as handle:
                self.assertEqual(list(csv.DictReader(handle)), [])

    def test_stage_2_emerging_passes_uptrend_gate(self):
        result = self.screen([self.candidate(is_stage_2=False, stage_2_rules_passed=5)])
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["eligible"])

    def test_stage_2_insufficient_rules_rejected(self):
        result = self.screen([self.candidate(is_stage_2=False, stage_2_rules_passed=4)])
        self.assertEqual(result, [])

    def test_near_benchmark_within_tolerance_passes(self):
        result = self.screen([self.candidate(relative_return_3m=-0.02)])
        self.assertEqual(len(result), 1)
        self.assertTrue(result[0]["eligible"])


if __name__ == "__main__":
    unittest.main()
