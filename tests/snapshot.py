"""Build small arb-scanner-style snapshots for tests (same pegged_quotes table)."""
import sqlite3

SCHEMA = """
CREATE TABLE pegged_quotes (ts REAL, block INTEGER, pair TEXT, pool TEXT, bid REAL, ask REAL);
CREATE TABLE episodes (id INTEGER PRIMARY KEY, pair TEXT);
"""

# Hand-worked rows. Price = B per 1 A.
#   ts 1_000_020 (minute 16667): best bid 1.0002 (B), best ask 0.9999 (A)
#       mid 1.00005 -> +0.5 bp, gap 1.0002/0.9999 - 1 = +3.0 bp; dead pool C never wins
#   ts 1_000_080 (minute 16668): mid 0.9997 -> -3.0 bp, gap 0.9996/0.9998 - 1 = -2.0 bp
#   ts 1_000_140 (minute 16669): no ask anywhere -> dropped
#   (minute 16670 missing: scanner was down)
#   ts 1_000_260 (minute 16671): mid 1.0000 -> 0.0 bp, gap -2.0 bp
HAND_ROWS = [
    (1_000_020.0, 1, "cbBTC/WBTC", "A", 0.9997, 0.9999),
    (1_000_020.0, 1, "cbBTC/WBTC", "B", 1.0002, 1.0004),
    (1_000_020.0, 1, "cbBTC/WBTC", "C", 0.03, 270.0),
    (1_000_080.0, 2, "cbBTC/WBTC", "A", 0.9996, 0.9998),
    (1_000_140.0, 3, "cbBTC/WBTC", "A", 0.9996, None),
    (1_000_260.0, 5, "cbBTC/WBTC", "A", 0.9999, 1.0001),
    (1_000_020.0, 1, "USDT/USDC", "A", 0.9999, 1.0001),
]


def make_snapshot(path, rows=HAND_ROWS):
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO pegged_quotes VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def rows_from_mids(pair, mids_bp, start_ts=1_000_020.0, half_spread=1e-4):
    """One pool, one row per minute, bid/ask placed evenly around each mid (given in bp)."""
    rows = []
    for i, m in enumerate(mids_bp):
        mid = 1.0 + m / 1e4
        rows.append((start_ts + 60 * i, i, pair, "A", mid - half_spread, mid + half_spread))
    return rows
