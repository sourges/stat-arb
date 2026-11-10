"""load.py against a hand-built snapshot (rows and numbers in tests/snapshot.py)."""
import math
import os
import sqlite3
import tempfile
import unittest
from unittest import mock

import load
from tests.snapshot import make_snapshot


class TestLoad(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "snap.sqlite")
        make_snapshot(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_best_across_pools_per_minute(self):
        df = load.load_pegged(self.path)
        btc = df[df["pair"] == "cbBTC/WBTC"].reset_index(drop=True)
        self.assertEqual(len(btc), 3)                                   # one-sided minute dropped
        first = btc.iloc[0]
        self.assertEqual((first["bid_pool"], first["ask_pool"]), ("B", "A"))   # dead pool C never wins
        self.assertAlmostEqual(first["mid_bp"], 0.5, places=6)
        self.assertAlmostEqual(first["gap_bp"], 3.0003, places=3)
        self.assertAlmostEqual(btc.iloc[1]["mid_bp"], -3.0, places=6)
        self.assertAlmostEqual(btc.iloc[1]["gap_bp"], -2.0004, places=3)
        self.assertAlmostEqual(btc.iloc[2]["mid_bp"], 0.0, places=6)

    def test_filters(self):
        self.assertEqual(len(load.load_pegged(self.path, since_ts=1_000_050)), 2)
        self.assertEqual(set(load.load_pegged(self.path, pairs=["USDT/USDC"])["pair"]), {"USDT/USDC"})
        self.assertTrue(load.load_pegged(self.path, since_ts=2_000_000).empty)

    def test_minute_series_keeps_missed_minutes_as_nan(self):
        s = load.minute_series(load.load_pegged(self.path), "cbBTC/WBTC")
        self.assertEqual(len(s), 5)                                     # minutes 16667..16671
        vals = s.tolist()
        self.assertAlmostEqual(vals[0], 0.5, places=6)
        self.assertAlmostEqual(vals[1], -3.0, places=6)
        self.assertTrue(math.isnan(vals[2]) and math.isnan(vals[3]))    # one-sided + scanner down
        self.assertAlmostEqual(vals[4], 0.0, places=6)
        self.assertEqual(str(s.index.tz), "UTC")

    def test_snapshot_is_read_only(self):
        conn = load.connect_snapshot(self.path)
        try:
            with self.assertRaises(sqlite3.OperationalError):
                conn.execute("INSERT INTO pegged_quotes VALUES (1, 1, 'x', 'y', 1, 1)")
        finally:
            conn.close()

    def test_missing_snapshot_is_an_error_and_creates_nothing(self):
        missing = os.path.join(self.tmp.name, "nope.sqlite")
        with self.assertRaises(FileNotFoundError):
            load.load_pegged(missing)
        self.assertFalse(os.path.exists(missing))

    def test_load_closes_the_file(self):
        # Windows can't move or delete an open file, so a leaked connection would block
        # replacing the snapshot with a newer one.
        opened = []
        real_connect = load.sqlite3.connect

        def spy(*args, **kwargs):
            conn = real_connect(*args, **kwargs)
            opened.append(conn)
            return conn

        with mock.patch.object(load.sqlite3, "connect", spy):
            load.load_pegged(self.path)
        self.assertEqual(len(opened), 1)
        with self.assertRaises(sqlite3.ProgrammingError):   # closed connections refuse queries
            opened[0].execute("SELECT 1")


if __name__ == "__main__":
    unittest.main()
