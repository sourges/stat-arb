"""Backtest a z-score rotation on ONE pegged pool, trading its real bid/ask.

The idea (pair A/B, price = B per 1 A, e.g. USDT/USDC):
  z = how stretched the pool's mid is versus its last --window minutes (stats.rolling_zscore,
      measured against the window BEFORE each minute: no peeking).

  "cheap" book, holds B (USDC) at rest:
      z < -entry   A is unusually cheap  -> buy A at the ASK
      z >= -exit   back to normal        -> sell A at the BID, back in B
  "rich" book, holds A (USDT) at rest:
      z > +entry   A is unusually rich   -> sell A at the BID
      z <= +exit   back to normal        -> buy A back at the ASK
  --side both runs the two books side by side, each with --size of its own.

  No shorting, no borrowing: each book just swaps between the two tokens on one pool.
  Each book's result is measured in its own home token (USDC or USDT), as bp of --size.

Honesty rules built in:
  - Signal at minute t, fill at minute t + delay (default 1): you see a quote, then trade.
    If that minute has no quote, the order waits for the next one that does.
  - Fills use the pool's own bid/ask at --size: $100 quotes, or the depth quotes at the
    bigger size (only logged since Nov 14, 2026; minutes without them can't fill).
  - Gas is charged per swap (--gas, in USD; 2 swaps per round trip).
  - --max-hold closes a position that hasn't come back after that many minutes.
  - A position still open at the end is closed at the last quote and marked "end".

Run:
  python backtest.py                                    USDT/USDC on aero1-ts1, defaults
  python backtest.py --pair USDT/USDC --pool univ3-100
  python backtest.py --window 240 --entry 2 --exit 0 --size 1000
  python backtest.py --sweep                            try several windows x entries
"""
import argparse
import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

import config
import load
import stats


@dataclass
class Trade:
    book: str            # "cheap" (home B) or "rich" (home A)
    entry_time: object
    exit_time: object
    entry_px: float      # cheap: ask paid for A; rich: bid received for A
    exit_px: float       # cheap: bid received for A; rich: ask paid for A
    hold_min: float
    gross_bp: float      # before gas
    gas_bp: float
    net_bp: float
    net_usd: float
    reason: str          # "revert", "max hold" or "end"


def fill_prices(bars, size_usd):
    """(bid, ask) series to trade at: $100 quotes, or the depth quotes for a bigger size.

    A bigger size uses depth quotes only where they were taken at exactly that size;
    other minutes are NaN, i.e. can't fill.
    """
    if size_usd <= config.QUOTE_SIZE_USD:
        return bars["bid"], bars["ask"]
    at_size = np.isclose(bars["depth_usd"].astype(float), size_usd)
    return bars["depth_bid"].where(at_size), bars["depth_ask"].where(at_size)


def _round_trip(book, entry_px, exit_px):
    """Gross result in bp of the home token.
    cheap: spend 1 B on A at entry_px (ask), sell it at exit_px (bid): exit / entry - 1
    rich:  sell 1 A for entry_px B (bid), buy A back at exit_px (ask): entry / exit - 1
    """
    if book == "cheap":
        return (exit_px / entry_px - 1) * 1e4
    return (entry_px / exit_px - 1) * 1e4


def simulate(bars, window, entry_z, exit_z=0.0, side="both", size_usd=100.0,
             gas_usd=0.001, delay=1, max_hold=None):
    """Run the rotation over minute bars (from load.load_pool). Returns [Trade]."""
    if entry_z <= exit_z:
        raise ValueError("entry must be further out than exit")
    if delay < 0:
        raise ValueError("delay can't be negative")
    bid, ask = fill_prices(bars, size_usd)
    bid, ask = bid.to_numpy(float), ask.to_numpy(float)
    z = stats.rolling_zscore(bars["mid_bp"].astype(float), window).to_numpy(float)
    times = bars.index
    n = len(bars)
    gas_bp = 2 * gas_usd / size_usd * 1e4
    books = {"cheap": side in ("both", "cheap"), "rich": side in ("both", "rich")}
    if not any(books.values()):
        raise ValueError("side must be both, cheap or rich")

    trades = []
    for book, on in books.items():
        if not on:
            continue
        # entry fills at the price that takes A on/off: cheap buys at ask, rich sells at bid
        entry_side, exit_side = (ask, bid) if book == "cheap" else (bid, ask)
        state = {"holding": False, "pending": None, "entry_i": None, "entry_px": None}
        # pending = (earliest fill index, close reason or None for an open)

        def try_fill(i):
            """Fill the pending order at minute i if it's due and the pool has a quote."""
            due_i, reason = state["pending"]
            if i < due_i:
                return
            px = (exit_side if state["holding"] else entry_side)[i]
            if math.isnan(px):
                return                                  # no quote this minute: keep waiting
            if not state["holding"]:
                state.update(holding=True, entry_i=i, entry_px=px)
            else:
                gross = _round_trip(book, state["entry_px"], px)
                trades.append(_trade(book, times, state["entry_i"], i, state["entry_px"], px,
                                     gross, gas_bp, size_usd, reason))
                state["holding"] = False
            state["pending"] = None

        for i in range(n):
            # 1) an order from an earlier minute fills once its minute comes
            if state["pending"] is not None:
                try_fill(i)
            if state["pending"] is not None:
                continue                                # nothing new until it fills
            # 2) new signal. Max hold doesn't need z (a flat window has no z at all).
            holding = state["holding"]
            if holding and max_hold is not None and i - state["entry_i"] >= max_hold:
                state["pending"] = (i + delay, "max hold")
            elif not math.isnan(z[i]):
                stretched = z[i] < -entry_z if book == "cheap" else z[i] > entry_z
                normal = z[i] >= -exit_z if book == "cheap" else z[i] <= exit_z
                if not holding and stretched:
                    state["pending"] = (i + delay, None)
                elif holding and normal:
                    state["pending"] = (i + delay, "revert")
            # 3) delay 0: fill at this same minute's quote
            if state["pending"] is not None and delay == 0:
                try_fill(i)
        holding, entry_i, entry_px = state["holding"], state["entry_i"], state["entry_px"]
        if holding:                                   # close at the last quote we have
            last = np.flatnonzero(~np.isnan(exit_side))
            if last.size and last[-1] >= entry_i:
                j = last[-1]
                gross = _round_trip(book, entry_px, exit_side[j])
                trades.append(_trade(book, times, entry_i, j, entry_px, exit_side[j], gross,
                                     gas_bp, size_usd, "end"))
    return sorted(trades, key=lambda t: t.entry_time)


def _trade(book, times, i, j, entry_px, exit_px, gross, gas_bp, size_usd, reason):
    net = gross - gas_bp
    hold = (times[j] - times[i]).total_seconds() / 60
    return Trade(book, times[i], times[j], float(entry_px), float(exit_px), hold, float(gross),
                 float(gas_bp), float(net), float(net / 1e4 * size_usd), reason)


def summarize(trades):
    """Totals for a list of trades (empty list -> zero trades)."""
    if not trades:
        return {"trades": 0, "wins": 0, "win_pct": None, "avg_net_bp": None, "total_bp": 0.0,
                "total_usd": 0.0, "avg_hold_min": None, "worst_bp": None, "max_hold_exits": 0}
    net = [t.net_bp for t in trades]
    wins = sum(1 for x in net if x > 0)
    return {"trades": len(trades), "wins": wins, "win_pct": 100 * wins / len(trades),
            "avg_net_bp": sum(net) / len(net), "total_bp": sum(net),
            "total_usd": sum(t.net_usd for t in trades),
            "avg_hold_min": sum(t.hold_min for t in trades) / len(trades),
            "worst_bp": min(net), "max_hold_exits": sum(t.reason == "max hold" for t in trades)}


def _f(v, spec):
    return "-" if v is None else format(v, spec)


def print_run(trades, args, bars, list_n=15):
    days = (bars.index[-1] - bars.index[0]).total_seconds() / 86400 if len(bars) > 1 else 0
    print(f"BACKTEST  {args.pair} on {args.pool}  window {args.window} min  entry |z|>{args.entry}"
          f"  exit {args.exit}  size ${args.size:,.0f}  gas ${args.gas}/swap  delay {args.delay} min"
          f"  max hold {args.max_hold or 'none'}")
    print(f"data {days:.1f} days ({bars['bid'].notna().sum()} minutes with quotes)")
    if args.size > config.QUOTE_SIZE_USD:
        print("  (fills at this size use depth quotes, which start Nov 14, 2026)")
    print(f"{'book':<7}{'trades':>7}{'win %':>7}{'avg net bp':>12}{'total bp':>10}{'total $':>10}"
          f"{'avg hold':>10}{'worst bp':>10}{'max-hold':>10}")
    for name in ("cheap", "rich", "all"):
        sub = trades if name == "all" else [t for t in trades if t.book == name]
        if name != "all" and args.side not in ("both", name):
            continue
        s = summarize(sub)
        print(f"{name:<7}{s['trades']:>7}{_f(s['win_pct'], '.0f'):>7}{_f(s['avg_net_bp'], '+.2f'):>12}"
              f"{s['total_bp']:>+10.2f}{s['total_usd']:>+10.4f}{_f(s['avg_hold_min'], '.0f'):>10}"
              f"{_f(s['worst_bp'], '+.2f'):>10}{s['max_hold_exits']:>10}")
    if trades and list_n:
        print(f"\nLAST {min(list_n, len(trades))} TRADES (local time)")
        print(f"{'book':<7}{'entry':<13}{'exit':<13}{'in px':>10}{'out px':>10}{'hold':>7}"
              f"{'gross':>8}{'net bp':>8}  reason")
        for t in trades[-list_n:]:
            fmt = "%m-%d %H:%M"
            print(f"{t.book:<7}{t.entry_time.tz_convert(LOCAL_TZ).strftime(fmt):<13}"
                  f"{t.exit_time.tz_convert(LOCAL_TZ).strftime(fmt):<13}{t.entry_px:>10.6f}"
                  f"{t.exit_px:>10.6f}{t.hold_min:>7.0f}{t.gross_bp:>+8.2f}{t.net_bp:>+8.2f}  {t.reason}")
    print("\nOne week of one pool. Treat a good result as 'worth watching', not proof.")


def print_sweep(bars, args, windows=(60, 240, 720, 1440), entries=(1.5, 2.0, 2.5, 3.0)):
    print(f"SWEEP  {args.pair} on {args.pool}  size ${args.size:,.0f}  side {args.side}"
          f"  (total net bp / trades)")
    print(f"{'window':>8}" + "".join(f"{'entry ' + str(e):>16}" for e in entries))
    for w in windows:
        cells = []
        for e in entries:
            s = summarize(simulate(bars, w, e, args.exit, args.side, args.size, args.gas,
                                   args.delay, args.max_hold))
            cells.append(f"{s['total_bp']:+.1f} / {s['trades']}")
        print(f"{w:>8}" + "".join(f"{c:>16}" for c in cells))
    print("\nPicking the best cell on the same week it was tested on flatters it. "
          "Prefer settings that are good across neighbours, then check them on new data.")


LOCAL_TZ = "America/New_York"


def main(argv=None):
    ap = argparse.ArgumentParser(description="z-score rotation backtest on one pegged pool.")
    ap.add_argument("snapshot", nargs="?", default=config.SNAPSHOT_PATH)
    ap.add_argument("--pair", default=config.BT_PAIR)
    ap.add_argument("--pool", default=config.BT_POOL)
    ap.add_argument("--window", type=int, default=config.BT_WINDOW_MIN)
    ap.add_argument("--entry", type=float, default=config.BT_ENTRY_Z)
    ap.add_argument("--exit", type=float, default=config.BT_EXIT_Z)
    ap.add_argument("--side", choices=["both", "cheap", "rich"], default="both")
    ap.add_argument("--size", type=float, default=config.BT_SIZE_USD)
    ap.add_argument("--gas", type=float, default=config.BT_GAS_USD, help="USD per swap")
    ap.add_argument("--delay", type=int, default=config.BT_DELAY_MIN)
    ap.add_argument("--max-hold", type=int, default=config.BT_MAX_HOLD_MIN)
    ap.add_argument("--trades", type=int, default=15, help="how many recent trades to list")
    ap.add_argument("--sweep", action="store_true")
    args = ap.parse_args(argv)

    bars = load.load_pool(args.snapshot, args.pair, args.pool)
    if bars["bid"].notna().sum() == 0:
        pools = load.pools_for(args.snapshot, args.pair)
        print(f"no quotes for {args.pair} on {args.pool}. Pools recorded: {', '.join(pools) or 'none'}")
        return
    if args.sweep:
        print_sweep(bars, args)
    else:
        print_run(simulate(bars, args.window, args.entry, args.exit, args.side, args.size,
                           args.gas, args.delay, args.max_hold), args, bars, args.trades)


if __name__ == "__main__":
    main()
