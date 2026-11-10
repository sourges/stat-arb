"""stats.py with hand-worked numbers.

rolling z-score (the lesson example), window 5:
    window [2, -1, 1, 0, -2], new value 6
    mean 0, sample std sqrt(10 / 4) = 1.581  ->  z = 6 / 1.581 = 3.795
    include_current: window [-1, 1, 0, -2, 6], mean 0.8,
        squared devs 3.24 + 0.04 + 0.64 + 7.84 + 27.04 = 38.8, std sqrt(38.8 / 4) = 3.114
        z = (6 - 0.8) / 3.114 = 1.670

half-life: fit  change = a + b * previous
    [16, 8, 4, 2, 1]          every change is -0.5 * previous  -> b = -0.5
                              h = -ln2 / ln(0.5) = 1.0 step
    [8, 6, 4.5, 3.375, ...]   change = -0.25 * previous        -> b = -0.25
                              h = -ln2 / ln(0.75) = 2.409 steps
    [1, 2, 4, 8, 16]          change = +1 * previous: no pull back -> inf

log spread: ln(110) - ln(100) = 0.09531 (about a 10% gap)
            beta 2: ln(110) - 2 ln(100) = -4.50986
"""
import math
import unittest

import numpy as np
import pandas as pd

import stats


def ar1(phi, n=500, seed=7):
    """x[t] = phi * x[t-1] + noise. phi 0.5 reverts fast; phi 1 is a random walk."""
    rng = np.random.default_rng(seed)
    noise = rng.normal(size=n)
    x = np.zeros(n)
    for i in range(1, n):
        x[i] = phi * x[i - 1] + noise[i]
    return pd.Series(x)


class TestLogSpread(unittest.TestCase):
    def test_hand_worked(self):
        self.assertAlmostEqual(stats.log_spread(110.0, 100.0), 0.0953102, places=6)
        self.assertAlmostEqual(stats.log_spread(110.0, 100.0, beta=2.0), -4.5098600, places=6)

    def test_works_on_series(self):
        out = stats.log_spread(pd.Series([100.0, 101.0]), pd.Series([100.0, 100.0]))
        self.assertAlmostEqual(out.iloc[0], 0.0)
        self.assertAlmostEqual(out.iloc[1], math.log(1.01))


class TestZScore(unittest.TestCase):
    def setUp(self):
        self.s = pd.Series([2.0, -1.0, 1.0, 0.0, -2.0, 6.0])

    def test_against_prior_window(self):
        z = stats.rolling_zscore(self.s, 5)
        self.assertAlmostEqual(z.iloc[5], 3.795, places=3)
        self.assertTrue(z.iloc[:5].isna().all())          # no full prior window yet

    def test_including_current_dilutes_the_extreme(self):
        z = stats.rolling_zscore(self.s, 5, include_current=True)
        self.assertAlmostEqual(z.iloc[5], 1.670, places=3)


class TestHalfLife(unittest.TestCase):
    def test_halving_series_is_one_step(self):
        s = pd.Series([16.0, 8.0, 4.0, 2.0, 1.0])
        self.assertAlmostEqual(stats.reversion_speed(s), -0.5)
        self.assertAlmostEqual(stats.half_life(s), 1.0)

    def test_quarter_pull_back(self):
        s = pd.Series([8.0 * 0.75 ** i for i in range(6)])
        self.assertAlmostEqual(stats.half_life(s), 2.409, places=3)

    def test_trending_series_never_reverts(self):
        self.assertEqual(stats.half_life(pd.Series([1.0, 2.0, 4.0, 8.0, 16.0])), math.inf)

    def test_missed_minute_is_skipped_not_bridged(self):
        # 4 -> NaN -> 10: the 4->10 jump is NOT used; the halving pairs still give b = -0.5
        s = pd.Series([16.0, 8.0, 4.0, np.nan, 10.0, 5.0, 2.5])
        self.assertAlmostEqual(stats.half_life(s), 1.0)

    def test_too_few_values(self):
        with self.assertRaises(ValueError):
            stats.half_life(pd.Series([2.0, 1.0, 0.5]))       # only 2 changes


class TestADF(unittest.TestCase):
    def test_mean_reverting_series_has_tiny_p(self):
        self.assertLess(stats.adf_pvalue(ar1(0.5)), 0.01)

    def test_random_walk_has_large_p(self):
        self.assertGreater(stats.adf_pvalue(ar1(1.0)), 0.10)

    def test_needs_enough_varying_data(self):
        with self.assertRaises(ValueError):
            stats.adf_pvalue(pd.Series(range(10), dtype=float))
        with self.assertRaises(ValueError):
            stats.adf_pvalue(pd.Series([1.0] * 50))

    def test_half_life_of_fast_ar1_is_about_one(self):
        # phi 0.5 -> b = -0.5 in theory -> 1 step; random noise moves the estimate a little
        self.assertAlmostEqual(stats.half_life(ar1(0.5)), 1.0, delta=0.2)


if __name__ == "__main__":
    unittest.main()
