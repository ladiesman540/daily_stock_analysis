# -*- coding: utf-8 -*-
"""Tests for scripts/doctor.py (offline)."""

import unittest

from src.config import ConfigIssue

from scripts.doctor import FAIL, INFO, OK, WARN, Report, classify_issue


class ReportTestCase(unittest.TestCase):
    def test_no_fail_means_ok_verdict(self):
        report = Report()
        report.add("LLM", OK, "model resolved")
        report.add("Notifications", WARN, "no channel")
        self.assertFalse(report.has_fail)
        rendered = report.render()
        self.assertIn("== LLM ==", rendered)
        self.assertIn("no blocking problems", rendered)

    def test_fail_item_flips_verdict_and_renders_hint(self):
        report = Report()
        report.add("LLM", FAIL, "token expired", hint="run `codex login`")
        self.assertTrue(report.has_fail)
        rendered = report.render()
        self.assertIn("[FAIL]", rendered)
        self.assertIn("fix: run `codex login`", rendered)
        self.assertIn("need attention", rendered)

    def test_to_json_shape(self):
        report = Report()
        report.add("Database", INFO, "created on first run")
        payload = report.to_json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["sections"]["Database"][0]["level"], INFO)


class ClassifyIssueTestCase(unittest.TestCase):
    def test_stock_list_error_downgraded_when_watchlist_present(self):
        issue = ConfigIssue(severity="error", message="未配置自选股列表", field="STOCK_LIST")
        self.assertEqual(classify_issue(issue, watchlist_nonempty=True), INFO)

    def test_stock_list_error_kept_without_watchlist(self):
        issue = ConfigIssue(severity="error", message="未配置自选股列表", field="STOCK_LIST")
        self.assertEqual(classify_issue(issue, watchlist_nonempty=False), FAIL)

    def test_other_severities_map_directly(self):
        error = ConfigIssue(severity="error", message="no model", field="LITELLM_CONFIG")
        warning = ConfigIssue(severity="warning", message="x", field="Y")
        info = ConfigIssue(severity="info", message="x", field="Z")
        self.assertEqual(classify_issue(error, watchlist_nonempty=True), FAIL)
        self.assertEqual(classify_issue(warning, watchlist_nonempty=True), WARN)
        self.assertEqual(classify_issue(info, watchlist_nonempty=True), INFO)


if __name__ == "__main__":
    unittest.main()
