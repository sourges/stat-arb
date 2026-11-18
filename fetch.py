"""Fetch Kraken candles into candles.sqlite (stat-arb's own database; safe to re-run).

Run on the MAIN PC, in stat-arb with the venv active:
  python fetch.py                                   every pair x interval in config.FETCH_*
  python fetch.py --pair ETHXBT --interval 60       just one
  python fetch.py --import ETHXBT_60.csv --pair ETHXBT --interval 60
                                                    load one of Kraken's full-history CSV files
  python fetch.py --list                            what's stored

The API gives only the newest 720 candles, so hourly history grows by running this again
at least every 30 days (4-hour: 120 days, daily: ~2 years). Each run adds the candles
finished since the last one.
"""
import argparse
import time

import candles
import config
import kraken


def fetch_one(conn, pair, interval, get=kraken.http_get_json):
    """Fetch new finished candles for one pair/interval. Returns (written, newest ts)."""
    since = candles.last_ts(conn, "kraken", pair, interval)
    rows = kraken.fetch_ohlc(pair, interval, since=since, get=get)
    written = candles.insert(conn, "kraken", pair, interval, rows)
    return written, candles.last_ts(conn, "kraken", pair, interval)


def fmt_ts(ts):
    return time.strftime("%Y-%m-%d %H:%M", time.gmtime(ts)) + " UTC" if ts else "-"


def print_list(conn):
    print(f"{'source':<8}{'pair':<10}{'interval':>9}{'candles':>9}  {'first':<22}{'last':<22}")
    for source, pair, interval, n, first, last in candles.summary(conn):
        print(f"{source:<8}{pair:<10}{interval:>9}{n:>9}  {fmt_ts(first):<22}{fmt_ts(last):<22}")


def main(argv=None, get=kraken.http_get_json, sleep=time.sleep):
    ap = argparse.ArgumentParser(description="Fetch Kraken candles into candles.sqlite.")
    ap.add_argument("--db", default=config.CANDLES_PATH)
    ap.add_argument("--pair")
    ap.add_argument("--interval", type=int)
    ap.add_argument("--import", dest="import_csv", metavar="CSV")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args(argv)

    conn = candles.connect(args.db)
    try:
        if args.list:
            print_list(conn)
            return
        if args.import_csv:
            if not args.pair or not args.interval:
                ap.error("--import needs --pair and --interval")
            n = candles.insert(conn, "kraken", args.pair, args.interval, kraken.read_csv(args.import_csv))
            print(f"imported {n} candles: {args.pair} {args.interval} min from {args.import_csv}")
            return
        pairs = [args.pair] if args.pair else config.FETCH_PAIRS
        intervals = [args.interval] if args.interval else config.FETCH_INTERVALS
        failed = 0
        for i, (pair, interval) in enumerate((p, iv) for p in pairs for iv in intervals):
            if i:
                sleep(config.FETCH_PAUSE_S)      # be polite to the public API
            try:
                n, newest = fetch_one(conn, pair, interval, get)
                print(f"{pair:<10}{interval:>6} min  +{n:<5} newest {fmt_ts(newest)}")
            except Exception as e:               # one bad pair shouldn't stop the rest
                failed += 1
                print(f"{pair:<10}{interval:>6} min  FAILED: {type(e).__name__}: {e}")
        print()
        print_list(conn)
        if failed:
            print(f"\n{failed} fetch(es) failed; re-run later, nothing was lost.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
