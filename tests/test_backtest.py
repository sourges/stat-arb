"""backtest.py and load.load_pool with hand-worked numbers.

Bars: one per minute, bid = 1 + (mid - 0.5) / 10,000, ask = 1 + (mid + 0.5) / 10,000
(a 1 bp round trip), mid given in bp.  Window 3, entry |z| > 1, exit z back to 0,
$100, gas $0.001 per swap -> gas = 2 x 0.001 / 100 = 0.2 bp per round trip.

CHEAP book, mids 0, 1, -1, 0, -6, -6, 0, 2, 1   (minutes 0..8)
    z at 4: window [1, -1, 0] mean 0, std 1        -> z = -6        stretched: buy
        fills at minute 5 (delay 1): ask(-6) = 0.99945
    z at 5: window [-1, 0, -6] mean -2.333, std 3.215 -> z = -1.14   not back yet
    z at 6: window [0, -6, -6] mean -4, std 3.464   -> z = +1.15    back: sell
        fills at minute 7: bid(2) = 1.00015
    gross = 1.00015 / 0.99945 - 1 = 7.0039 bp, net = 7.0039 - 0.2 = 6.8039 bp = $0.068039

RICH book, the same mids with the sign flipped: sell at bid(6) = 1.00055 at minute 5,
    buy back at ask(-2) = 0.99985 at minute 7: gross = 1.00055 / 0.99985 - 1 = 7.0011 bp

DELAY 0: the cheap trade fills at the signal minutes instead: buy at minute 4 ask(-6) = 0.99945,
    sell at minute 6 bid(0) = 0.99995: gross = 0.99995 / 0.99945 - 1 = 5.0028 bp

MAX HOLD 3, mids 0, 1, -1, 0, -6, -6, -6, -6, -6, -6: bought at minute 5 and the price never
    comes back (from minute 7 the window is flat, so there is no z at all). At minute 8 it has
    been held 3 minutes -> sell at minute 9 bid(-6) = 0.99935: gross = -1.0006 bp, "max hold".
    Without max hold it's closed at the last quote (minute 9, same price) and marked "end".
"""
import io
import math
import os
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stdout

import numpy as np
import pandas as pd

import backtest as bt
import load
from tests.snapshot import make_snapshot, rows_from_mids

MIDS = [0, 1, -1, 0, -6, -6, 0, 2, 1]
FLAT_AFTER_DROP = [0, 1, -1, 0, -6, -6, -6, -6, -6, -6]


def bars(mids, depth=None):
    """Minute bars like load.load_pool returns. depth: (size, half-spread bp) for depth quotes."""
    idx = pd.date_range("2026-11-10 00:00", periods=len(mids), freq="1min", tz="UTC")
    m = pd.Series(mids, index=idx, dtype=float)
    df = pd.DataFrame({"bid": 1 + (m - 0.5) / 1e4, "ask": 1 + (m + 0.5) / 1e4,
                       "depth_usd": np.nan, "depth_bid": np.nan, "depth_ask": np.nan, "mid_bp": m})
    if depth:
        size, half = depth
        df["depth_usd"] = size
        df["depth_bid"] = 1 + (m - half) / 1e4
        df["depth_ask"] = 1 + (m + half) / 1e4
    return df


def run(b, **kw):
    args = dict(window=3, entry_z=1.0, exit_z=0.0, side="cheap", size_usd=100.0, gas_usd=0.001, delay=1)
    args.update(kw)
    return bt.simulate(b, **args)


class TestRoundTrips(unittest.TestCase):
    def test_cheap_book_hand_worked(self):
        (t,) = run(bars(MIDS))
        self.assertEqual((t.book, t.reason, t.hold_min), ("cheap", "revert", 2.0))
        self.assertEqual((t.entry_time.minute, t.exit_time.minute), (5, 7))
        self.assertAlmostEqual(t.entry_px, 0.99945, places=9)
        self.assertAlmostEqual(t.exit_px, 1.00015, places=9)
        self.assertAlmostEqual(t.gross_bp, 7.0039, places=4)
        self.assertAlmostEqual(t.gas_bp, 0.2, places=9)
        self.assertAlmostEqual(t.net_bp, 6.8039, places=4)
        self.assertAlmostEqual(t.net_usd, 0.068039, places=6)

    def test_rich_book_hand_worked(self):
        (t,) = run(bars([-x for x in MIDS]), side="rich")
        self.assertEqual((t.book, t.reason), ("rich", "revert"))
        self.assertAlmostEqual(t.entry_px, 1.00055, places=9)       # sold A at the bid
        self.assertAlmostEqual(t.exit_px, 0.99985, places=9)        # bought it back at the ask
        self.assertAlmostEqual(t.gross_bp, 7.0011, places=4)

    def test_cheap_book_skips_the_spike_but_takes_the_snap_back(self):
        # mids 0, -1, 1, 0, 6, 6, 0, -2, -1: the spike at minute 4 is a RICH signal, so the
        # cheap book does nothing there. But at minute 6 the drop back to 0 is itself unusually
        # low against the window [0, 6, 6] (z = -1.15), so the cheap book buys at minute 7.
        (t,) = run(bars([-x for x in MIDS]), side="cheap")
        self.assertEqual(t.entry_time.minute, 7)
        self.assertAlmostEqual(t.entry_px, 0.99985, places=9)     # ask(-2)
        (t,) = run(bars(MIDS), side="rich")                        # mirror image
        self.assertEqual(t.entry_time.minute, 7)
        self.assertAlmostEqual(t.entry_px, 1.00015, places=9)     # bid(2)

    def test_both_runs_each_book(self):
        b = bars(MIDS + [3, 9, 9, 0, -3, 0])      # the dip, then a spike the rich book takes
        books = sorted(t.book for t in run(b, side="both"))
        self.assertIn("cheap", books)
        self.assertIn("rich", books)
        self.assertEqual(len(run(b, side="both")),
                         len(run(b, side="cheap")) + len(run(b, side="rich")))

    def test_gas_scales_with_size(self):
        b = bars(MIDS, depth=(1000.0, 0.5))
        (t,) = run(b, size_usd=1000.0)
        self.assertAlmostEqual(t.gas_bp, 0.02, places=9)            # 2 x 0.001 / 1000
        self.assertAlmostEqual(t.net_usd, (7.0039 - 0.02) / 1e4 * 1000, places=5)


class TestTiming(unittest.TestCase):
    def test_delay_zero_fills_at_the_signal_minute(self):
        (t,) = run(bars(MIDS), delay=0)
        self.assertEqual((t.entry_time.minute, t.exit_time.minute), (4, 6))
        self.assertAlmostEqual(t.exit_px, 0.99995, places=9)
        self.assertAlmostEqual(t.gross_bp, 5.0028, places=4)

    def test_delay_two_waits_two_minutes(self):
        # signal at 4 -> buy at 6 ask(0) = 1.00005; back to normal at 6 -> sell at 8 bid(1) = 1.00005
        (t,) = run(bars(MIDS), delay=2)
        self.assertEqual((t.entry_time.minute, t.exit_time.minute), (6, 8))
        self.assertAlmostEqual(t.entry_px, 1.00005, places=9)
        self.assertAlmostEqual(t.gross_bp, 0.0, places=6)

    def test_exit_level_other_than_the_mean(self):
        # entry 2, exit 1.2: the cheap book sells once z >= -1.2. At minute 5 z = -1.14, so it
        # sells at minute 6 bid(0) = 0.99995 (gross 5.0028 bp) instead of waiting for z >= 0.
        (t,) = run(bars(MIDS), entry_z=2.0, exit_z=1.2)
        self.assertEqual(t.exit_time.minute, 6)
        self.assertAlmostEqual(t.gross_bp, 5.0028, places=4)
        # mirror: the rich book buys back once z <= +1.2
        (t,) = run(bars([-x for x in MIDS]), side="rich", entry_z=2.0, exit_z=1.2)
        self.assertEqual(t.exit_time.minute, 6)

    def test_order_waits_for_a_minute_with_a_quote(self):
        b = bars(MIDS)
        b.loc[b.index[5], ["bid", "ask", "mid_bp"]] = np.nan     # pool missing at minute 5
        (t,) = run(b)
        self.assertEqual(t.entry_time.minute, 6)                  # waited one minute
        self.assertAlmostEqual(t.entry_px, 1.00005, places=9)     # ask(0)
        self.assertEqual((t.reason, t.exit_time.minute), ("end", 8))   # the gap hid every later z

    def test_max_hold_closes_without_any_z(self):
        (t,) = run(bars(FLAT_AFTER_DROP), max_hold=3)
        self.assertEqual((t.reason, t.entry_time.minute, t.exit_time.minute), ("max hold", 5, 9))
        self.assertAlmostEqual(t.gross_bp, -1.0006, places=4)

    def test_open_position_closed_at_the_end(self):
        (t,) = run(bars(FLAT_AFTER_DROP))
        self.assertEqual((t.reason, t.exit_time.minute), ("end", 9))
        self.assertAlmostEqual(t.exit_px, 0.99935, places=9)

    def test_signal_needs_a_full_window(self):
        self.assertEqual(run(bars([0, -9, 0])), [])               # no prior window of 3 yet

    def test_bad_settings(self):
        with self.assertRaises(ValueError):
            run(bars(MIDS), entry_z=0.0, exit_z=0.0)
        with self.assertRaises(ValueError):
            run(bars(MIDS), side="sideways")
        with self.assertRaises(ValueError):
            run(bars(MIDS), delay=-1)


class TestSizes(unittest.TestCase):
    def test_bigger_size_trades_the_depth_quotes(self):
        (t,) = run(bars(MIDS, depth=(1000.0, 2.0)), size_usd=1000.0)
        self.assertAlmostEqual(t.entry_px, 1 + (-6 + 2.0) / 1e4, places=9)   # 0.99960
        self.assertAlmostEqual(t.exit_px, 1 + (2 - 2.0) / 1e4, places=9)     # 1.00000
        self.assertAlmostEqual(t.gross_bp, (1.0 / 0.9996 - 1) * 1e4, places=6)

    def test_no_depth_quotes_means_no_fills(self):
        self.assertEqual(run(bars(MIDS), size_usd=1000.0), [])

    def test_depth_at_another_size_does_not_fill(self):
        self.assertEqual(run(bars(MIDS, depth=(1000.0, 0.5)), size_usd=2500.0), [])


class TestSummary(unittest.TestCase):
    def test_totals(self):
        trades = run(bars(MIDS)) + run(bars(FLAT_AFTER_DROP))
        s = bt.summarize(trades)
        self.assertEqual((s["trades"], s["wins"]), (2, 1))
        self.assertAlmostEqual(s["win_pct"], 50.0)
        self.assertAlmostEqual(s["total_bp"], 6.8039 - 1.2006, places=4)
        self.assertAlmostEqual(s["avg_net_bp"], (6.8039 - 1.2006) / 2, places=4)
        self.assertAlmostEqual(s["worst_bp"], -1.2006, places=4)
        self.assertAlmostEqual(s["avg_hold_min"], 3.0)            # (2 + 4) / 2

    def test_empty(self):
        self.assertEqual(bt.summarize([])["trades"], 0)


class TestLoadPool(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_old_snapshot_without_depth_columns(self):
        path = os.path.join(self.tmp.name, "old.sqlite")
        rows = rows_from_mids("USDT/USDC", [-9.0, -10.0])
        rows.append((rows[0][0], 0, "USDT/USDC", "B", 0.5, 2.0))     # another pool, same minute
        make_snapshot(path, rows[:1] + rows[2:] + [(rows[1][0] + 60, 1, "USDT/USDC", "A",
                                                    rows[1][4], rows[1][5])])
        b = load.load_pool(path, "USDT/USDC", "A")
        self.assertEqual(len(b), 3)                                   # minute 1 missing -> NaN row
        self.assertAlmostEqual(b["mid_bp"].iloc[0], -9.0, places=6)
        self.assertTrue(math.isnan(b["bid"].iloc[1]))
        self.assertAlmostEqual(b["mid_bp"].iloc[2], -10.0, places=6)
        self.assertTrue(b["depth_bid"].isna().all())
        self.assertEqual(load.pools_for(path, "USDT/USDC"), ["A", "B"])

    def test_new_snapshot_with_depth(self):
        path = os.path.join(self.tmp.name, "new.sqlite")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE pegged_quotes (ts REAL, block INTEGER, pair TEXT, pool TEXT,"
                     " bid REAL, ask REAL, depth_usd REAL, depth_bid REAL, depth_ask REAL)")
        conn.execute("INSERT INTO pegged_quotes VALUES (1000020, 1, 'USDT/USDC', 'A',"
                     " 0.9990, 0.9992, 1000, 0.9989, 0.9993)")
        conn.commit()
        conn.close()
        b = load.load_pool(path, "USDT/USDC", "A")
        self.assertEqual((b["depth_usd"].iloc[0], b["depth_bid"].iloc[0]), (1000.0, 0.9989))
        self.assertAlmostEqual(b["mid_bp"].iloc[0], -9.0, places=6)
        self.assertTrue(load.load_pool(path, "USDT/USDC", "nope").empty)


class TestCommandLine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "snap.sqlite")
        mids = (MIDS + [3, 9, 9, 0, -3, 0]) * 3
        make_snapshot(self.path, rows_from_mids("USDT/USDC", mids, half_spread=0.5e-4))

    def tearDown(self):
        self.tmp.cleanup()

    def out(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            bt.main([self.path, "--pool", "A", *argv])
        return buf.getvalue()

    def test_run_prints_books_and_trades(self):
        out = self.out("--window", "3", "--entry", "1")
        self.assertIn("BACKTEST  USDT/USDC on A  window 3 min", out)
        for word in ("cheap", "rich", "all", "LAST", "revert"):
            self.assertIn(word, out)

    def test_sweep(self):
        out = self.out("--sweep")
        self.assertIn("SWEEP", out)
        self.assertIn("entry 2.5", out)

    def test_unknown_pool_lists_the_real_ones(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            bt.main([self.path, "--pool", "zzz"])
        self.assertIn("Pools recorded: A", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
