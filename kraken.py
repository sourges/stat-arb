"""Kraken candles: the public OHLC API and Kraken's downloadable CSV files.

Public API (no key):  GET https://api.kraken.com/0/public/OHLC?pair=ETHXBT&interval=60[&since=ts]
    result = {"<Kraken's internal pair name>": [[time, open, high, low, close, vwap, volume, count], ...],
              "last": <id to pass as since next time>}
    - prices arrive as strings, time and count as integers
    - at most the 720 NEWEST candles, whatever 'since' says (daily: ~2 years, hourly: 30 days)
    - the LAST candle in the list is the one still forming: it is dropped here, never stored

CSV files (Kraken support: "Downloadable historical OHLCVT data", full history, updated quarterly):
    one file per pair and interval, no header, columns: time, open, high, low, close, volume, trades
"""
import csv
import json
import urllib.parse
import urllib.request

API_URL = "https://api.kraken.com/0/public/OHLC"
INTERVALS = (1, 5, 15, 30, 60, 240, 1440, 10080, 21600)


class KrakenError(Exception):
    pass


def http_get_json(url, timeout=20):
    """Plain HTTPS GET -> parsed JSON. Tests pass a fake instead."""
    req = urllib.request.Request(url, headers={"User-Agent": "stat-arb/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ohlc_url(pair, interval, since=None):
    params = {"pair": pair, "interval": int(interval)}
    if since is not None:
        params["since"] = int(since)
    return API_URL + "?" + urllib.parse.urlencode(params)


def parse_ohlc(reply):
    """API reply -> finished candles [(ts, open, high, low, close, vwap, volume, trades)], oldest first.

    Raises KrakenError if Kraken reports an error or the reply has no candle list.
    """
    if not isinstance(reply, dict):
        raise KrakenError("reply is not a JSON object")
    if reply.get("error"):
        raise KrakenError("; ".join(reply["error"]))
    result = reply.get("result")
    if not isinstance(result, dict):
        raise KrakenError("reply has no result")
    lists = [v for k, v in result.items() if k != "last" and isinstance(v, list)]
    if len(lists) != 1:
        raise KrakenError(f"expected one candle list, got {len(lists)}")
    rows = []
    for c in lists[0][:-1]:                      # last one is still forming: drop it
        ts, o, h, l, cl, vwap, vol, n = c
        rows.append((int(ts), float(o), float(h), float(l), float(cl), float(vwap), float(vol), int(n)))
    return sorted(rows)


def fetch_ohlc(pair, interval, since=None, get=http_get_json):
    """Finished candles from the API (newest 720 at most), only those after `since` if given."""
    if int(interval) not in INTERVALS:
        raise ValueError(f"Kraken intervals are {INTERVALS}")
    rows = parse_ohlc(get(ohlc_url(pair, interval, since)))
    if since is not None:
        rows = [r for r in rows if r[0] > since]
    return rows


def read_csv(path):
    """A Kraken OHLCVT CSV file -> [(ts, open, high, low, close, None, volume, trades)].

    No header. Timestamps in milliseconds (if a file has them) are converted to seconds.
    Blank lines are skipped; a malformed line raises ValueError with its line number.
    """
    rows = []
    with open(path, newline="") as f:
        for n, line in enumerate(csv.reader(f), start=1):
            if not line or not "".join(line).strip():
                continue
            if len(line) < 7:
                raise ValueError(f"{path} line {n}: expected 7 columns, got {len(line)}")
            try:
                ts = int(float(line[0]))
                if ts > 10_000_000_000:          # milliseconds
                    ts //= 1000
                o, h, l, c, v = (float(x) for x in line[1:6])
                trades = int(float(line[6]))
            except ValueError as e:
                raise ValueError(f"{path} line {n}: {e}") from None
            rows.append((ts, o, h, l, c, None, v, trades))
    return sorted(rows)
