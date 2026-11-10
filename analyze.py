"""Does each pegged pair actually mean-revert? Read-only report on an arb-scanner snapshot.

Run:  python analyze.py                               default snapshot (config.SNAPSHOT_PATH)
      python analyze.py D:\\data\\snap.sqlite           a snapshot somewhere else
      python analyze.py --hours 24                    last 24 hours only
      python analyze.py --window 120                  z-score over 120 minutes

Columns, per pair (all in bp away from 1.0 unless noted):
  minutes    minutes with both a bid and an ask
  mean/std   where the mid usually sits and how much it wiggles
  min/max    the extremes seen
  ADF p      < 0.05 = it keeps coming back to its mean (good for stat arb)
  half-life  minutes for a stretch to shrink by half ("-" if too little data)
  now        latest mid,   z = how unusual "now" is versus the last --window minutes
  gap>0      minutes where one pool's bid beat another pool's ask (before gas)
"""
import argparse
import math
import time

import config
import load
import stats


def pair_stats(df, pair, window):
    """Everything the report prints for one pair. Values that need more data are None."""
    rows = df[df["pair"] == pair]
    mid = load.minute_series(df, pair)          # regular 1-minute grid, NaN = missed minute
    out = {
        "pair": pair,
        "minutes": len(rows),
        "first_ts": float(rows["ts"].min()),
        "last_ts": float(rows["ts"].max()),
        "mean": float(rows["mid_bp"].mean()),
        "std": float(rows["mid_bp"].std()) if len(rows) > 1 else None,
        "min": float(rows["mid_bp"].min()),
        "max": float(rows["mid_bp"].max()),
        "now": float(rows["mid_bp"].iloc[-1]),
        "gap_pos": int((rows["gap_bp"] > 0).sum()),
        "adf_p": None,
        "half_life": None,
        "z": None,
    }
    try:
        out["adf_p"] = stats.adf_pvalue(mid)
    except ValueError:
        pass
    try:
        out["half_life"] = stats.half_life(mid)
    except ValueError:
        pass
    z = stats.rolling_zscore(mid, window).iloc[-1]
    if not math.isnan(z):
        out["z"] = float(z)
    return out


def verdict(st, min_minutes, p_max):
    if st["adf_p"] is None:
        return "too little data"
    word = "reverts" if st["adf_p"] < p_max else "no sign of reverting"
    if st["minutes"] < min_minutes:
        word += " (little data)"
    return word


def fmt(v, spec, none="-"):
    if v is None:
        return none
    if isinstance(v, float) and math.isinf(v):
        return "never"
    return format(v, spec)


def print_report(df, window, min_minutes=config.MIN_MINUTES, p_max=config.ADF_PVALUE_MAX):
    print(f"PEGGED PAIRS  (mid in bp away from 1.0, z over {window} min)")
    print(f"{'pair':<13}{'minutes':>8}{'mean':>8}{'std':>7}{'min':>8}{'max':>8}{'ADF p':>8}"
          f"{'half-life':>11}{'now':>8}{'z':>7}{'gap>0':>7}  verdict")
    if df.empty:
        print("  no recorder data in this snapshot")
        return
    for pair in sorted(df["pair"].unique()):
        st = pair_stats(df, pair, window)
        hl = fmt(st["half_life"], ".1f")
        print(f"{pair:<13}{st['minutes']:>8}{st['mean']:>+8.1f}{fmt(st['std'], '.1f'):>7}"
              f"{st['min']:>+8.1f}{st['max']:>+8.1f}{fmt(st['adf_p'], '.3f'):>8}"
              f"{hl + (' min' if hl not in ('-', 'never') else ''):>11}{st['now']:>+8.1f}"
              f"{fmt(st['z'], '+.2f'):>7}{st['gap_pos']:>7}  {verdict(st, min_minutes, p_max)}")
    first = time.strftime("%m-%d %H:%M", time.localtime(df["ts"].min()))
    last = time.strftime("%m-%d %H:%M", time.localtime(df["ts"].max()))
    print(f"\ndata {first} -> {last} (local time). A day is the least that says anything; a week is better.")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Mean-reversion report on an arb-scanner snapshot.")
    ap.add_argument("snapshot", nargs="?", default=config.SNAPSHOT_PATH)
    ap.add_argument("--hours", type=float, default=None)
    ap.add_argument("--window", type=int, default=config.Z_WINDOW_MIN, help="z-score window, minutes")
    args = ap.parse_args(argv)
    since = time.time() - args.hours * 3600 if args.hours else None
    print_report(load.load_pegged(args.snapshot, since_ts=since), args.window)


if __name__ == "__main__":
    main()
