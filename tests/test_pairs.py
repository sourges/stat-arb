"""Hedge ratio, cointegration and pairs.py.

Hedge ratio, hand-worked (least squares of ln A on ln B):
    ln B = 1, 2, 3, 4 and ln A = 1 + 2 ln B             -> alpha 1, beta 2 exactly
    ln A = 0.5 + 1.5 ln B + (+0.1, -0.1, -0.1, +0.1)    -> alpha 0.5, beta 1.5 exactly
        (the noise sums to 0 and is uncorrelated with ln B: centred ln B is -1.5,-0.5,0.5,1.5,
         and -0.15 + 0.05 - 0.05 + 0.15 = 0, so the fit can't lean either way)

Spread stats, hand-worked, window 3, spread 0, 0.01, -0.01, 0, 0.05 (log units):
    recent window [0.01, -0.01, 0]: mean 0, sample std 0.01 -> recent std 1.00 %
    z = (0.05 - 0) / 0.01 = 5.0, stretch at |z|=2 = 2 x 1.00 = 2.00 %

Synthetic market (seeded, 400 daily candles):
    ln BTC = random walk (2% a day); ln ETH = ln BTC + ln 0.05 + e, e[t] = 0.5 e[t-1] + 1% noise
    -> beta ~ 1, the ratio reverts (ADF p ~ 0) with a half-life of ~1 day (0.5 per step)
    Unrelated ETH (its own random walk) -> no sign of reverting.
Fees at 0.30% maker: ratio 2 orders = 0.60%, hedged 4 orders = 1.20%.
"""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout

import numpy as np
import pandas as pd

import candles
import pairs
import stats

T0 = 1_700_000_000 - 1_700_000_000 % 86400


def synthetic(n=400, seed=11, related=True, beta=1.0):
    rng = np.random.default_rng(seed)
    lb = np.log(60000) + np.cumsum(rng.normal(0, 0.02, n))
    if related:
        e = np.zeros(n)
        noise = rng.normal(0, 0.01, n)
        for i in range(1, n):
            e[i] = 0.5 * e[i - 1] + noise[i]
        la = beta * lb + np.log(0.05) + e
    else:
        la = np.log(3000) + np.cumsum(rng.normal(0, 0.02, n))
    return la, lb


def write_db(path, la, lb, interval=1440, direct=True):
    conn = candles.connect(path)
    step = interval * 60
    def rows(lp):
        return [(T0 + i * step, None, None, None, float(np.exp(v)), None, 1.0, 1) for i, v in enumerate(lp)]
    candles.insert(conn, "kraken", "ETHUSD", interval, rows(la))
    candles.insert(conn, "kraken", "XBTUSD", interval, rows(lb))
    if direct:
        candles.insert(conn, "kraken", "ETHXBT", interval, rows(la - lb))
    conn.close()


class TestHedgeRatio(unittest.TestCase):
    def test_exact_line(self):
        b = pd.Series([1.0, 2.0, 3.0, 4.0])
        alpha, beta = stats.hedge_ratio(1 + 2 * b, b)
        self.assertAlmostEqual(alpha, 1.0)
        self.assertAlmostEqual(beta, 2.0)

    def test_balanced_noise_does_not_move_the_fit(self):
        b = pd.Series([1.0, 2.0, 3.0, 4.0])
        alpha, beta = stats.hedge_ratio(0.5 + 1.5 * b + pd.Series([0.1, -0.1, -0.1, 0.1]), b)
        self.assertAlmostEqual(alpha, 0.5)
        self.assertAlmostEqual(beta, 1.5)

    def test_uses_only_shared_times(self):
        a = pd.Series([3.0, 5.0, 7.0, 100.0], index=[0, 1, 2, 9])      # 9 has no B
        b = pd.Series([1.0, 2.0, 3.0, 4.0], index=[0, 1, 2, 3])        # 3 has no A
        self.assertAlmostEqual(stats.hedge_ratio(a, b)[1], 2.0)

    def test_bad_input(self):
        with self.assertRaises(ValueError):
            stats.hedge_ratio(pd.Series([1.0, 2.0]), pd.Series([1.0, 2.0]))
        with self.assertRaises(ValueError):
            stats.hedge_ratio(pd.Series([1.0, 2.0, 3.0]), pd.Series([5.0, 5.0, 5.0]))


class TestCoint(unittest.TestCase):
    def test_related_vs_unrelated(self):
        la, lb = synthetic()
        self.assertLess(stats.coint_pvalue(pd.Series(la), pd.Series(lb)), 0.01)
        la, lb = synthetic(related=False)
        self.assertGreater(stats.coint_pvalue(pd.Series(la), pd.Series(lb)), 0.10)

    def test_needs_20_points(self):
        with self.assertRaises(ValueError):
            stats.coint_pvalue(pd.Series(range(10), dtype=float), pd.Series(range(10), dtype=float))


class TestSpreadStats(unittest.TestCase):
    def test_hand_worked(self):
        st = pairs.spread_stats(pd.Series([0.0, 0.01, -0.01, 0.0, 0.05]), window=3, adf=False)
        self.assertAlmostEqual(st["recent_std_pct"], 1.0)
        self.assertAlmostEqual(st["z"], 5.0)
        self.assertIsNone(st["adf_p"])

    def test_round_trip(self):
        self.assertAlmostEqual(pairs.round_trip_pct(2, 0.30), 0.60)
        self.assertAlmostEqual(pairs.round_trip_pct(4, 0.30), 1.20)


class TestReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "c.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def out(self, *argv):
        buf = io.StringIO()
        with redirect_stdout(buf):
            pairs.main(["--db", self.db, *argv])
        return buf.getvalue()

    def test_related_pair(self):
        write_db(self.db, *synthetic())
        res = pairs.analyze(self.db, "ETHUSD", "XBTUSD", "ETHXBT", 1440, 30)
        self.assertEqual(res["candles"], 400)
        self.assertAlmostEqual(res["beta"], 1.0, delta=0.05)
        self.assertLess(res["ratio"]["adf_p"], 0.01)
        self.assertLess(res["hedged"]["adf_p"], 0.01)
        self.assertAlmostEqual(res["ratio"]["half_life"], 1.0, delta=0.25)
        self.assertEqual(res["direct_candles"], 400)
        # the ETHXBT market IS the ratio here, so its stats match the ratio's
        self.assertAlmostEqual(res["direct_stats"]["std_pct"], res["ratio"]["std_pct"], places=9)

    def test_beta_two_needs_the_hedge(self):
        # ln ETH = 2 ln BTC + const + e: the hedged spread is just e (reverts, ~1 day half-life,
        # ~1.15% std), but the plain ratio ln ETH - ln BTC still carries one full ln BTC: it wanders.
        write_db(self.db, *synthetic(beta=2.0), direct=False)
        res = pairs.analyze(self.db, "ETHUSD", "XBTUSD", None, 1440, 30)
        self.assertAlmostEqual(res["beta"], 2.0, delta=0.05)
        self.assertAlmostEqual(res["hedged"]["half_life"], 1.0, delta=0.25)
        self.assertLess(res["hedged"]["std_pct"], 2.0)
        self.assertGreater(res["ratio"]["std_pct"], 10.0)
        self.assertGreater(res["ratio"]["half_life"], 20.0)

    def test_report_text(self):
        write_db(self.db, *synthetic())
        out = self.out("--maker", "0.30")
        self.assertIn("PAIR  ETHUSD vs XBTUSD  1440-min candles, z over the last 30 candles", out)
        self.assertIn("(fitted on all of it: hindsight)", out)
        lines = {line.split()[0] + " " + line.split()[1]: line for line in out.splitlines()
                 if line.startswith(("ratio", "hedged", "ETHXBT"))}
        self.assertIn("0.60", lines["ratio (beta"])
        self.assertIn("1.20", lines["hedged (beta"])
        self.assertIn("reverts", lines["ratio (beta"])
        self.assertIn("days", lines["ratio (beta"])
        self.assertIn("ETHXBT market", out)

    def test_unrelated_pair(self):
        write_db(self.db, *synthetic(related=False), direct=False)
        out = self.out()
        self.assertIn("no sign of reverting", out)
        self.assertNotIn("ETHXBT market", out)

    def test_four_hour_half_life_in_hours(self):
        write_db(self.db, *synthetic(), interval=240)
        out = self.out("--interval", "240")
        ratio = next(line for line in out.splitlines() if line.startswith("ratio"))
        hours = float(ratio.split("hours")[0].split()[-1])
        self.assertTrue(2.0 < hours < 6.0, ratio)     # ~0.8-1 candle x 4 hours

    def test_too_few_candles(self):
        candles.connect(self.db).close()
        self.assertIn("only 0 candles both have; run fetch.py first", self.out())


if __name__ == "__main__":
    unittest.main()
