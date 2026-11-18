"""stat-arb settings. Edit on the main PC and push.

Data comes from arb-scanner SNAPSHOTS, never the live database:
on the 24/7 PC, in arb-scanner:   python export_db.py
then copy arb_scanner_snapshot.sqlite into this folder (or pass its path to analyze.py).
"""

# Default snapshot location (analyze.py also takes a path argument).
SNAPSHOT_PATH = "arb_scanner_snapshot.sqlite"

# Rolling window for the z-score, in minutes (the recorder logs one row a minute).
Z_WINDOW_MIN = 60

# Below this many minutes of data, analyze.py marks a pair "(little data)":
# a day is the least that says anything; a week is better.
MIN_MINUTES = 1440

# A pair "reverts" in the report when the ADF test p-value is below this.
ADF_PVALUE_MAX = 0.05

# ---- backtest.py defaults (all can be changed on the command line) ----------------
# Trade ONE deep pool's own bid/ask (not the best-across-pools mid).
BT_PAIR = "USDT/USDC"
BT_POOL = "aero1-ts1"          # ~0.2 bp round trip at $100, ~0.5 bp at $1,000
BT_WINDOW_MIN = 240            # rolling window for z, minutes
BT_ENTRY_Z = 2.0               # enter when |z| is beyond this
BT_EXIT_Z = 0.0                # exit when z is back to this (0 = the rolling mean)
BT_SIZE_USD = 100.0            # > QUOTE_SIZE_USD uses the recorder's depth quotes
BT_GAS_USD = 0.001             # per swap; arb-scanner shows ~$0.0003-0.001 per leg
BT_DELAY_MIN = 1               # signal at minute t fills at minute t + 1
BT_MAX_HOLD_MIN = 1440         # give up after a day; None = hold until it reverts

# The size of arb-scanner's normal pegged quotes (bid/ask columns).
QUOTE_SIZE_USD = 100.0
