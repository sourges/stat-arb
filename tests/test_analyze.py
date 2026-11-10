"""analyze.py on the tiny hand snapshot and on a longer synthetic one.

Tiny snapshot, cbBTC/WBTC mids: +0.5, -3.0, 0.0 bp (3 minutes)
    mean -0.833, min -3.0, max +0.5, gap>0 in 1 minute
    too few values for ADF (needs 20) or half-life -> "too little data"

Synthetic: 300 minutes of x[t] = 0.5 x[t-1] + noise (in bp) -> reverts fast,
    ADF p tiny, half-life about 1 minute, but 300 < 1440 minutes -> "(little data)".
"""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import analyze
import load
import stats
from tests.snapshot import make_snapshot, rows_from_mids
from tests.test_stats import ar1


class TestAnalyze(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def snap(self, rows=None):
        path = os.path.join(self.tmp.name, "snap.sqlite")
        make_snapshot(path, rows) if rows is not None else make_snapshot(path)
        return path

    def test_tiny_snapshot_numbers(self):
        st = analyze.pair_stats(load.load_pegged(self.snap()), "cbBTC/WBTC", window=60)
        self.assertEqual((st["minutes"], st["gap_pos"]), (3, 1))
        self.assertAlmostEqual(st["mean"], -0.8333, places=3)
        self.assertAlmostEqual(st["min"], -3.0, places=6)
        self.assertAlmostEqual(st["max"], 0.5, places=6)
        self.assertAlmostEqual(st["now"], 0.0, places=6)
        self.assertIsNone(st["adf_p"])
        self.assertIsNone(st["half_life"])
        self.assertIsNone(st["z"])
        self.assertEqual(analyze.verdict(st, 1440, 0.05), "too little data")

    def test_reverting_pair(self):
        mids = (ar1(0.5, n=300) * 2).tolist()            # wiggles of a few bp
        df = load.load_pegged(self.snap(rows_from_mids("USDT/USDC", mids)))
        st = analyze.pair_stats(df, "USDT/USDC", window=60)
        self.assertEqual(st["minutes"], 300)
        self.assertLess(st["adf_p"], 0.01)
        self.assertAlmostEqual(st["half_life"], 1.0, delta=0.2)
        expected_z = stats.rolling_zscore(load.minute_series(df, "USDT/USDC"), 60).iloc[-1]
        self.assertAlmostEqual(st["z"], expected_z, places=9)
        self.assertEqual(analyze.verdict(st, 1440, 0.05), "reverts (little data)")
        self.assertEqual(analyze.verdict(st, 100, 0.05), "reverts")

    def test_drifting_pair(self):
        mids = (ar1(1.0, n=300) * 0.3).tolist()           # random walk
        st = analyze.pair_stats(load.load_pegged(self.snap(rows_from_mids("DAI/USDC", mids))),
                                "DAI/USDC", window=60)
        self.assertEqual(analyze.verdict(st, 100, 0.05), "no sign of reverting")

    def test_main_prints_every_pair(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            analyze.main([self.snap(), "--window", "2"])
        out = buf.getvalue()
        self.assertIn("z over 2 min", out)
        self.assertIn("cbBTC/WBTC", out)
        self.assertIn("USDT/USDC", out)
        self.assertIn("too little data", out)

    def test_empty_snapshot(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            analyze.main([self.snap(rows=[])])
        self.assertIn("no recorder data", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
