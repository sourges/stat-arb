"""Read the pegged-pair recorder data out of an arb-scanner snapshot.

The snapshot is opened READ-ONLY (SQLite mode=ro): stat-arb can never change it,
and a missing path is an error instead of silently creating an empty database.

pegged_quotes holds one row per pool per minute. Price = B per 1 A (near 1.0):
    bid = what selling A gets, ask = what buying A costs (pool fee included)
Per pair and minute we keep the best across pools, the same way arb-scanner does:
    best_bid = highest bid, best_ask = lowest ask
    mid      = (best_bid + best_ask) / 2,   mid_bp = (mid - 1) * 10,000
    gap_bp   = (best_bid / best_ask - 1) * 10,000   (> 0: cross-pool profit before gas)
Dead pools (bid 0.03, ask 270) never win either side, so they drop out on their own.
A minute with no bid or no ask anywhere is left out.
"""
import os
import sqlite3

import pandas as pd

COLUMNS = ["time", "ts", "pair", "best_bid", "bid_pool", "best_ask", "ask_pool",
           "mid", "mid_bp", "gap_bp"]


def connect_snapshot(path):
    """Open a snapshot read-only. Raises FileNotFoundError if it isn't there."""
    if not os.path.exists(path):
        raise FileNotFoundError(f"no snapshot at {path}: run export_db.py in arb-scanner and copy it here")
    return sqlite3.connect(f"file:{os.path.abspath(path)}?mode=ro", uri=True)


def load_pegged(path, since_ts=None, pairs=None):
    """One row per pair per minute (see module docstring), sorted by pair then time."""
    conn = connect_snapshot(path)
    try:
        raw = pd.read_sql("SELECT ts, pair, pool, bid, ask FROM pegged_quotes WHERE ts >= ?",
                          conn, params=(since_ts or 0,))
    finally:
        conn.close()   # Windows can't move or delete an open file
    if pairs:
        raw = raw[raw["pair"].isin(pairs)]
    if raw.empty:
        return pd.DataFrame(columns=COLUMNS)

    bids = (raw.dropna(subset=["bid"]).sort_values("bid")
            .groupby(["pair", "ts"]).tail(1)[["pair", "ts", "bid", "pool"]]
            .rename(columns={"bid": "best_bid", "pool": "bid_pool"}))
    asks = (raw.dropna(subset=["ask"]).sort_values("ask")
            .groupby(["pair", "ts"]).head(1)[["pair", "ts", "ask", "pool"]]
            .rename(columns={"ask": "best_ask", "pool": "ask_pool"}))
    df = bids.merge(asks, on=["pair", "ts"], how="inner")   # inner: drops one-sided minutes
    df["mid"] = (df["best_bid"] + df["best_ask"]) / 2
    df["mid_bp"] = (df["mid"] - 1.0) * 1e4
    df["gap_bp"] = (df["best_bid"] / df["best_ask"] - 1.0) * 1e4
    df["time"] = pd.to_datetime(df["ts"], unit="s", utc=True)
    return df.sort_values(["pair", "ts"]).reset_index(drop=True)[COLUMNS]


def minute_series(df, pair, col="mid_bp"):
    """One pair's column on a regular 1-minute UTC grid.

    Minutes the recorder missed (scanner restarts, RPC hiccups) become NaN instead of
    being silently squeezed out, so "one step" always means one minute in stats.py.
    """
    s = df.loc[df["pair"] == pair].set_index("time")[col].astype(float)
    if s.empty:
        return s
    return s.resample("1min").last()


def load_pool(path, pair, pool, since_ts=None):
    """ONE pool's own quotes on a regular 1-minute UTC grid, for the backtest.

    Columns: bid, ask (at $100), depth_usd, depth_bid, depth_ask (at the bigger size;
    NaN before arb-scanner started logging depth on Nov 14, 2026, or when the pool
    couldn't fill it), mid_bp = ((bid + ask) / 2 - 1) * 10,000.
    Minutes the pool has no row become all-NaN rows, so one step is always one minute.
    """
    conn = connect_snapshot(path)
    try:
        cols = {row[1] for row in conn.execute("PRAGMA table_info(pegged_quotes)")}
        depth = "depth_usd, depth_bid, depth_ask" if "depth_bid" in cols else \
                "NULL AS depth_usd, NULL AS depth_bid, NULL AS depth_ask"
        df = pd.read_sql(f"SELECT ts, bid, ask, {depth} FROM pegged_quotes"
                         " WHERE pair = ? AND pool = ? AND ts >= ? ORDER BY ts",
                         conn, params=(pair, pool, since_ts or 0))
    finally:
        conn.close()
    cols = ["bid", "ask", "depth_usd", "depth_bid", "depth_ask", "mid_bp"]
    if df.empty:
        return pd.DataFrame(columns=cols, dtype=float)
    df = df.astype({c: float for c in cols if c != "mid_bp"})
    df["mid_bp"] = ((df["bid"] + df["ask"]) / 2 - 1.0) * 1e4
    df.index = pd.to_datetime(df.pop("ts"), unit="s", utc=True)
    return df.resample("1min").last()[cols]


def pools_for(path, pair):
    """Pool labels recorded for a pair, most rows first."""
    conn = connect_snapshot(path)
    try:
        return [r[0] for r in conn.execute(
            "SELECT pool FROM pegged_quotes WHERE pair = ? GROUP BY pool ORDER BY COUNT(*) DESC", (pair,))]
    finally:
        conn.close()
