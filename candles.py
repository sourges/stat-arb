"""stat-arb's OWN candle database (candles.sqlite), separate from arb-scanner's snapshots.

stat-arb never writes to a snapshot. Exchange history it fetches itself lives here:

    candles (source, pair, interval, ts, open, high, low, close, vwap, volume, trades)
        source    'kraken'
        pair      Kraken's name, e.g. 'ETHXBT', 'XBTUSD', 'ETHUSD'
        interval  candle length in minutes (60 = hourly, 1440 = daily)
        ts        candle START time, unix seconds UTC
        vwap      NULL for candles imported from Kraken's CSV files (they don't have it)

One row per (source, pair, interval, ts): fetching the same candle again replaces it, so
re-running fetch.py is always safe. Only finished (committed) candles are stored.
"""
import sqlite3

import pandas as pd

SCHEMA = """
CREATE TABLE IF NOT EXISTS candles (
    source    TEXT    NOT NULL,
    pair      TEXT    NOT NULL,
    interval  INTEGER NOT NULL,
    ts        INTEGER NOT NULL,
    open      REAL,
    high      REAL,
    low       REAL,
    close     REAL,
    vwap      REAL,
    volume    REAL,
    trades    INTEGER,
    PRIMARY KEY (source, pair, interval, ts)
);
"""


def connect(path):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    return conn


def insert(conn, source, pair, interval, rows):
    """rows: [(ts, open, high, low, close, vwap, volume, trades)]. Returns how many were written."""
    conn.executemany(
        "INSERT OR REPLACE INTO candles (source, pair, interval, ts, open, high, low, close,"
        " vwap, volume, trades) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        [(source, pair, int(interval), *r) for r in rows])
    conn.commit()
    return len(rows)


def last_ts(conn, source, pair, interval):
    """Start time of the newest stored candle, or None."""
    (ts,) = conn.execute("SELECT MAX(ts) FROM candles WHERE source=? AND pair=? AND interval=?",
                         (source, pair, int(interval))).fetchone()
    return ts


def summary(conn):
    """[(source, pair, interval, candles, first ts, last ts)] for everything stored."""
    return conn.execute("SELECT source, pair, interval, COUNT(*), MIN(ts), MAX(ts) FROM candles"
                        " GROUP BY source, pair, interval ORDER BY source, pair, interval").fetchall()


def load_close(path, pair, interval, source="kraken"):
    """Closing prices as a Series indexed by candle start time (UTC). Empty if none."""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        df = pd.read_sql("SELECT ts, close FROM candles WHERE source=? AND pair=? AND interval=?"
                         " ORDER BY ts", conn, params=(source, pair, int(interval)))
    finally:
        conn.close()
    s = pd.Series(df["close"].to_numpy(float), index=pd.to_datetime(df["ts"], unit="s", utc=True),
                  name=pair)
    return s
