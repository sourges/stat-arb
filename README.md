# stat-arb

Statistical arbitrage research. **Read-only: it never trades.** It answers one question
before any money moves: *does this pair keep coming back to its normal level, and how fast?*

Data comes from arb-scanner's pegged-pair recorder (cbBTC/WBTC, USDT/USDC, USDbC/USDC,
DAI/USDC: one quote per pool per minute on Base). stat-arb reads **snapshots** only,
never the live database, and opens them read-only.

## Setup (first time)

```
py -3.14 -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
python -m unittest            (expect: Ran 72 tests ... OK)
```

## Getting data

On the 24/7 PC, in the arb-scanner folder with its venv active:

```
python export_db.py
```

Copy `arb_scanner_snapshot.sqlite` into this folder on the main PC. (Never copy the live
`arb_scanner.sqlite`; it has `-wal`/`-shm` side files and can be mid-write.)
Snapshots are git-ignored.

## Running

```
python analyze.py                         uses arb_scanner_snapshot.sqlite here
python analyze.py D:\data\snap.sqlite     a snapshot somewhere else
python analyze.py --hours 24              last 24 hours only
python analyze.py --window 120            z-score over 120 minutes
```

Per pair, in bp away from 1.0 (price = B per 1 A):

| column | meaning |
|---|---|
| minutes | minutes with both a bid and an ask |
| mean / std | where the mid usually sits, and how much it wiggles |
| min / max | the extremes seen |
| ADF p | below 0.05 = it keeps returning to its mean (what stat arb needs) |
| half-life | minutes for a stretch to shrink by half; "never" = no pull back |
| now / z | latest mid, and how unusual it is versus the last `--window` minutes |
| gap>0 | minutes where one pool's bid beat another pool's ask (before gas) |
| verdict | reverts / no sign of reverting / too little data; "(little data)" under a day |

## Backtest

Trades ONE pool's own bid/ask (not the best-across-pools mid), as a rotation between
the two tokens: no shorting, no borrowing.

```
python backtest.py                                USDT/USDC on aero1-ts1, defaults in config.py
python backtest.py --window 720 --entry 2.5       other settings
python backtest.py --pool univ3-100               another pool
python backtest.py --size 1000                    trades the $1,000 depth quotes (from Nov 14, 2026)
python backtest.py --sweep                        windows 60/240/720/1440 x entries 1.5/2/2.5/3
python backtest.py --since 2026-11-17             only data from then on (New York time); --until too
```

- **cheap book** (holds USDC): buys USDT at the ask when z < -entry, sells at the bid when z is back to -exit.
- **rich book** (holds USDT): sells USDT at the bid when z > +entry, buys back at the ask when z is back to +exit.
- z is measured against the window *before* each minute; orders fill `--delay` minutes later
  (default 1) at that minute's quote, or the next minute that has one.
- Gas per swap (`--gas`, USD), `--max-hold` minutes before giving up, open positions closed at the end.
- Results are in bp of `--size` per book. One week of one pool: a good number means "test it
  again on next week's data", not "trade it". `--sweep` picks look better than they are.

## Kraken pairs (ETH vs BTC)

stat-arb's own history, in `candles.sqlite` (git-ignored). Fetched on the main PC:

```
python fetch.py                     ETHXBT, XBTUSD, ETHUSD x hourly, 4-hour, daily (config.FETCH_*)
python fetch.py --list              what's stored
python fetch.py --import ETHXBT_60.csv --pair ETHXBT --interval 60
                                    one of Kraken's full-history CSV files (optional, big download)
python pairs.py                     ETHUSD vs XBTUSD, daily
python pairs.py --interval 60 --window 168      hourly, z over the last week
```

- Kraken's free API returns only the newest 720 candles: daily ~2 years, 4-hour ~120 days,
  hourly ~30 days. Re-running `fetch.py` adds what finished since, so run it at least monthly
  to keep growing the hourly history. The candle still forming is never stored.
- `pairs.py` shows the spread two ways: the plain ratio (beta 1, i.e. the ETHXBT market: one
  order each way) and the hedged spread (beta fitted by least squares, tested with Engle-Granger
  cointegration; 4 orders per round trip if beta isn't 1). It compares a typical stretch
  (2 x recent std) with your maker round trip. beta is fitted on all the data (hindsight).

## Files

- `load.py` opens a snapshot read-only; best bid/ask across pools per minute; one pool's own quotes (`load_pool`); 1-minute grid (missed minutes = NaN)
- `stats.py` log spread, rolling z-score (against the window *before* each value), half-life, ADF test, hedge ratio, cointegration test
- `analyze.py` the report
- `backtest.py` z-score rotation on one pool, real bid/ask, gas, delay, max hold, sweep, --since/--until
- `kraken.py` Kraken OHLC API + CSV reader; `candles.py` candles.sqlite; `fetch.py` the fetch command
- `pairs.py` two-price pair report (hedge ratio, cointegration, half-life, stretch vs fees)
- `config.py` snapshot path, z window, thresholds. Edit on the main PC and push.

## Not included yet

Coinbase candle history; a backtest for two-price pairs (beta refitted as it goes, Kraken fees);
staked-ETH pairs.
