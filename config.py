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
