"""Two-price pair report from Kraken candles: does ETH vs BTC keep coming back, and would a
typical stretch pay for your Kraken fees?

Run (after fetch.py):
  python pairs.py                                  ETHUSD vs XBTUSD, daily candles
  python pairs.py --interval 60 --window 168       hourly, z over the last week
  python pairs.py --a ETHUSD --b XBTUSD --direct ETHXBT --interval 240 --window 180

Two ways to hold the spread, both shown:
  ratio (beta = 1)   ln ETH - ln BTC, i.e. the ETH/XBT market itself. ONE order each way
                     on Kraken's ETHXBT book. Nothing is estimated, so the plain ADF test applies.
  hedged (beta fit)  ln ETH - beta * ln BTC - alpha, beta from a least-squares fit.
                     If beta isn't 1 you'd need TWO markets (ETHUSD and XBTUSD): 4 orders per
                     round trip. Its test is Engle-Granger cointegration (stats.coint_pvalue).

Honest limits:
  - beta and alpha here are fitted on ALL the data, which a live trader wouldn't have had.
    That's fine for "is there a relationship at all?"; a backtest must refit as it goes.
  - "stretch at |z|=2" = 2 x the recent spread std: roughly what a reversion from z=2 back to
    0 could earn, before fees. It's compared with the maker round trip (fees only, no slippage).
"""
import argparse
import math

import numpy as np
import pandas as pd

import candles
import config
import stats

UNIT = {60: "hours", 240: "hours", 1440: "days", 10080: "weeks"}
PER_UNIT = {60: 1, 240: 4, 1440: 1, 10080: 1}    # candles -> unit: 4h candles count as 4 hours


def aligned_logs(path, a, b, interval):
    """ln close of A and B on the candle times both have."""
    sa = candles.load_close(path, a, interval)
    sb = candles.load_close(path, b, interval)
    df = pd.concat([np.log(sa), np.log(sb)], axis=1, join="inner").dropna()
    df.columns = ["a", "b"]
    return df


def spread_stats(spread, window, adf=True):
    """ADF p (or None), half-life in candles, std in %, last z, and recent std in % for one spread."""
    out = {"adf_p": None, "half_life": None, "z": None,
           "std_pct": float(spread.std() * 100), "recent_std_pct": None}
    if adf:
        try:
            out["adf_p"] = stats.adf_pvalue(spread)
        except ValueError:
            pass
    try:
        out["half_life"] = stats.half_life(spread)
    except ValueError:
        pass
    z = stats.rolling_zscore(spread, window).iloc[-1] if len(spread) > window else math.nan
    if not math.isnan(z):
        out["z"] = float(z)
    if len(spread) > window:
        out["recent_std_pct"] = float(spread.iloc[-window - 1:-1].std() * 100)
    return out


def analyze(path, a, b, direct, interval, window):
    """Everything the report prints, as a dict (None where there isn't enough data)."""
    logs = aligned_logs(path, a, b, interval)
    res = {"a": a, "b": b, "direct": direct, "interval": interval, "window": window,
           "candles": len(logs), "first": logs.index.min() if len(logs) else None,
           "last": logs.index.max() if len(logs) else None}
    if len(logs) < 20:
        return res
    alpha, beta = stats.hedge_ratio(logs["a"], logs["b"])
    res.update(alpha=alpha, beta=beta)
    ratio = logs["a"] - logs["b"]
    hedged = logs["a"] - beta * logs["b"] - alpha
    res["ratio"] = spread_stats(ratio, window)
    res["hedged"] = spread_stats(hedged, window, adf=False)
    res["hedged"]["adf_p"] = stats.coint_pvalue(logs["a"], logs["b"])
    if direct:
        d = candles.load_close(path, direct, interval)
        if len(d) >= 20:
            res["direct_stats"] = spread_stats(np.log(d), window)
            res["direct_candles"] = len(d)
    return res


def round_trip_pct(orders, fee_pct):
    return orders * fee_pct


def _f(v, spec, none="-"):
    if v is None:
        return none
    if isinstance(v, float) and math.isinf(v):
        return "never"
    return format(v, spec)


def _hl(st, interval):
    hl = st["half_life"]
    if hl is None or math.isinf(hl):
        return _f(hl, ".1f")
    return f"{hl * PER_UNIT.get(interval, 1):.1f} {UNIT.get(interval, 'candles')}"


def print_report(res, maker_pct=config.KRAKEN_MAKER_PCT):
    iv = res["interval"]
    print(f"PAIR  {res['a']} vs {res['b']}  {iv}-min candles, z over the last {res['window']} candles")
    if res["candles"] < 20:
        print(f"  only {res['candles']} candles both have; run fetch.py first (need 20+)")
        return
    print(f"data {res['first']:%Y-%m-%d %H:%M} -> {res['last']:%Y-%m-%d %H:%M} UTC, {res['candles']} candles")
    print(f"hedge ratio: ln {res['a']} = {res['alpha']:+.3f} + {res['beta']:.3f} x ln {res['b']}"
          f"   (fitted on all of it: hindsight)\n")
    rows = [("ratio (beta 1)", res["ratio"], 2, "ADF"), ("hedged (beta fit)", res["hedged"], 4, "coint")]
    if "direct_stats" in res:
        rows.append((f"{res['direct']} market", res["direct_stats"], 2, "ADF"))
    print(f"{'spread':<19}{'test p':>12}{'half-life':>14}{'std %':>8}{'now z':>8}"
          f"{'stretch@z2 %':>14}{'maker rt %':>12}  verdict")
    for name, st, orders, test in rows:
        stretch = 2 * st["recent_std_pct"] if st["recent_std_pct"] is not None else None
        cost = round_trip_pct(orders, maker_pct)
        verdict = []
        if st["adf_p"] is not None:
            verdict.append("reverts" if st["adf_p"] < config.ADF_PVALUE_MAX else "no sign of reverting")
        if stretch is not None:
            verdict.append("stretch > fees" if stretch > cost else "fees eat it")
        print(f"{name:<19}{_f(st['adf_p'], '.3f') + ' ' + test:>12}{_hl(st, iv):>14}"
              f"{st['std_pct']:>8.2f}{_f(st['z'], '+.2f'):>8}{_f(stretch, '.2f'):>14}"
              f"{cost:>12.2f}  {', '.join(verdict) or '-'}")
    print(f"\nmaker round trip = orders x {maker_pct}% (ratio and {res.get('direct') or 'direct'}: "
          "1 order in, 1 out; hedged: 2 markets x 2). Fees only, no slippage.")
    print("A pair worth backtesting needs both: 'reverts' AND 'stretch > fees'.")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Two-price pair report from Kraken candles.")
    ap.add_argument("--db", default=config.CANDLES_PATH)
    ap.add_argument("--a", default="ETHUSD")
    ap.add_argument("--b", default="XBTUSD")
    ap.add_argument("--direct", default="ETHXBT", help="market quoting A in B ('' for none)")
    ap.add_argument("--interval", type=int, default=1440)
    ap.add_argument("--window", type=int, default=30, help="z window, in candles")
    ap.add_argument("--maker", type=float, default=config.KRAKEN_MAKER_PCT, help="maker fee %%")
    args = ap.parse_args(argv)
    print_report(analyze(args.db, args.a, args.b, args.direct or None, args.interval, args.window),
                 args.maker)


if __name__ == "__main__":
    main()
