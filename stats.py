"""The math core of stat arb. Every function takes a pandas Series (one value per step).

log_spread       how far apart two prices are, as a % gap:  ln(A) - beta * ln(B)
rolling_zscore   how unusual the latest value is versus its recent past
half_life        how many steps it takes a stretch to shrink by half
adf_pvalue       does the series keep coming back (mean-reverting) or wander off?

For a pegged pair the recorder already gives the spread directly: mid_bp is the
gap from 1.0 in bp (beta = 1, because both tokens are the same asset on paper).
"""
import math

import numpy as np
import pandas as pd


def log_spread(price_a, price_b, beta=1.0):
    """ln(A) - beta * ln(B). A difference of logs is roughly a % gap,
    so 0.0003 means A is about 3 bp rich versus B."""
    return np.log(price_a) - beta * np.log(price_b)


def rolling_zscore(s, window, include_current=False):
    """z = (value - rolling mean) / rolling std, std with N-1 like pandas.

    include_current=False (the default) measures each value against the window
    BEFORE it. Including the value in its own window lets an extreme point dilute
    itself (6 after [2,-1,1,0,-2], window 5: z 3.79 against the window before it,
    only 1.67 against [-1,1,0,-2,6]), and in a backtest it quietly uses information
    you wouldn't have treated that way live.
    """
    mean = s.rolling(window).mean()
    std = s.rolling(window).std()
    if not include_current:
        mean, std = mean.shift(1), std.shift(1)
    return (s - mean) / std


def reversion_speed(s):
    """Fit  change = a + b * previous level  by least squares. Returns b.

    b < 0 means a high value tends to be followed by a fall (pulled back toward the mean).
    Steps where either value is NaN (a missed minute) are skipped.
    """
    prev = s.shift(1)
    change = s - prev
    ok = prev.notna() & change.notna()
    if ok.sum() < 3:
        raise ValueError("need at least 3 consecutive pairs of values")
    x = prev[ok].to_numpy(dtype=float)
    y = change[ok].to_numpy(dtype=float)
    design = np.column_stack([np.ones_like(x), x])
    (_a, b), *_ = np.linalg.lstsq(design, y, rcond=None)
    return float(b)


def half_life(s):
    """Steps for a stretch from the mean to shrink by half.

    Each step keeps (1 + b) of the stretch, so after h steps (1 + b)^h = 1/2:
        h = -ln 2 / ln(1 + b)
    b >= 0: no pull back at all -> inf.  b <= -1: gone within one step -> 0.
    """
    b = reversion_speed(s)
    if b >= 0:
        return math.inf
    if b <= -1:
        return 0.0
    return -math.log(2) / math.log(1 + b)


def adf_pvalue(s):
    """Augmented Dickey-Fuller test: the p-value for "this series is a random walk".

    Small p (< 0.05) = strong evidence it is NOT a random walk, i.e. it keeps returning
    to a mean: the thing stat arb needs. Large p = can't tell it apart from drifting.
    Missed minutes are dropped first. For a two-price pair with an estimated beta this
    becomes the Engle-Granger cointegration test (later); a pegged pair has beta = 1.
    """
    from statsmodels.tsa.stattools import adfuller   # slow import, only when needed

    x = pd.Series(s).dropna()
    if len(x) < 20:
        raise ValueError("need at least 20 values")
    if x.std() == 0:
        raise ValueError("series never changes")
    return float(adfuller(x.to_numpy(dtype=float), autolag="AIC", result_object=False)[1])


def hedge_ratio(log_a, log_b):
    """Least-squares fit  ln A = alpha + beta * ln B.  Returns (alpha, beta).

    beta answers: when B moves 1%, how many % does A usually move? The spread
    ln A - beta * ln B - alpha then strips out the part of A that just follows B.
    Only times where both series have a value are used.
    """
    df = pd.concat([pd.Series(log_a), pd.Series(log_b)], axis=1, join="inner").dropna()
    if len(df) < 3:
        raise ValueError("need at least 3 points where both prices exist")
    x = df.iloc[:, 1].to_numpy(dtype=float)
    y = df.iloc[:, 0].to_numpy(dtype=float)
    if np.ptp(x) == 0:
        raise ValueError("B never changes, so beta can't be estimated")
    design = np.column_stack([np.ones_like(x), x])
    (alpha, beta), *_ = np.linalg.lstsq(design, y, rcond=None)
    return float(alpha), float(beta)


def coint_pvalue(log_a, log_b):
    """Engle-Granger cointegration test p-value (statsmodels.coint).

    Same question as adf_pvalue ("does the spread keep coming back?"), but for a spread
    whose beta was ESTIMATED from the same data. Fitting beta makes the spread look calmer
    than it really is, so a plain ADF on it would say "reverts" too easily; coint uses
    stricter critical values for exactly that. Small p (< 0.05) = cointegrated.
    """
    from statsmodels.tsa.stattools import coint   # slow import, only when needed

    df = pd.concat([pd.Series(log_a), pd.Series(log_b)], axis=1, join="inner").dropna()
    if len(df) < 20:
        raise ValueError("need at least 20 points where both prices exist")
    return float(coint(df.iloc[:, 0].to_numpy(float), df.iloc[:, 1].to_numpy(float), trend="c")[1])
