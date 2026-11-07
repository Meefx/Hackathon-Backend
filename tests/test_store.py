import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

from app.store import connect, get_account, initialize, list_accounts, summarize


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = str(Path(self.directory.name) / "test.db")
        self.today = date(2026, 11, 7)
        initialize(self.path)

    def insert(self, days, amount=1000):
        with connect(self.path) as connection:
            connection.execute("INSERT INTO accounts VALUES (?, ?, ?, ?, ?, ?)", (
                f"CF-{days}", "Demo Borrower", f"LN-{days}", amount,
                (self.today - timedelta(days=days)).isoformat(), "Demo Agent",
            ))

    def test_aging_boundaries_and_future_due_date(self):
        for days, expected in [(-1, "current"), (0, "current"), (1, "early"),
                               (30, "early"), (31, "late"), (90, "late"), (91, "critical")]:
            with self.subTest(days=days):
                self.insert(days)
                account = get_account(self.path, f"CF-{days}", self.today)
                self.assertEqual(account["status"], expected)
                self.assertEqual(account["days_past_due"], max(0, days))

    def test_summary_excludes_current_accounts_from_overdue(self):
        self.insert(-3, 1000)
        self.insert(5, 2000)
        self.insert(95, 3000)
        summary = summarize(list_accounts(self.path, self.today))
        self.assertEqual(summary["total_outstanding"], 6000)
        self.assertEqual(summary["overdue_amount"], 5000)
        self.assertEqual(summary["overdue_accounts"], 2)
        self.assertEqual(sum(bucket["count"] for bucket in summary["buckets"]), 3)

    def test_missing_account_and_parameterized_lookup(self):
        self.insert(1)
        self.assertIsNone(get_account(self.path, "' OR 1=1 --", self.today))

    def test_empty_portfolio(self):
        summary = summarize(list_accounts(self.path, self.today))
        self.assertEqual(summary["total_accounts"], 0)
        self.assertEqual(summary["total_outstanding"], 0)


if __name__ == "__main__":
    unittest.main()
