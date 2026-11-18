"""Kraken candles: API parsing, CSV import, candles.sqlite, and fetch.py with a fake API.

Fake API reply (what Kraken sends): three candles, the last one still forming.
    [1700000000, "100.0", "101.0", "99.0", "100.5", "100.2", "12.5", 40]
    [1700003600, "100.5", "102.0", "100.0", "101.5", "101.0", "8.0", 25]
    [1700007200, "101.5", ...]            <- forming: must NOT be stored
    -> 2 finished candles, prices as floats, ts and trades as ints.
A second run asks with since = 1700003600 (the newest stored) and stores only newer candles.
"""
import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest import mock

import candles
import config
import fetch
import kraken

C1 = [1700000000, "100.0", "101.0", "99.0", "100.5", "100.2", "12.5", 40]
C2 = [1700003600, "100.5", "102.0", "100.0", "101.5", "101.0", "8.0", 25]
C3 = [1700007200, "101.5", "103.0", "101.0", "102.5", "102.0", "5.0", 10]
C4 = [1700010800, "102.5", "103.5", "102.0", "103.0", "102.8", "4.0", 9]


def reply(*cs, key="XETHXXBT"):
    return {"error": [], "result": {key: [list(c) for c in cs], "last": cs[-1][0]}}


class FakeKraken:
    """Answers OHLC URLs from a dict {pair: [candles]}; remembers every URL asked for."""

    def __init__(self, books, fail=()):
        self.books = books
        self.fail = set(fail)
        self.urls = []

    def __call__(self, url):
        self.urls.append(url)
        pair = url.split("pair=")[1].split("&")[0]
        if pair in self.fail:
            raise OSError("connection reset")
        return reply(*self.books[pair])


class TestParse(unittest.TestCase):
    def test_drops_the_forming_candle_and_converts_types(self):
        rows = kraken.parse_ohlc(reply(C1, C2, C3))
        self.assertEqual(rows, [(1700000000, 100.0, 101.0, 99.0, 100.5, 100.2, 12.5, 40),
                                (1700003600, 100.5, 102.0, 100.0, 101.5, 101.0, 8.0, 25)])

    def test_errors(self):
        with self.assertRaises(kraken.KrakenError) as e:
            kraken.parse_ohlc({"error": ["EQuery:Unknown asset pair"], "result": {}})
        self.assertIn("Unknown asset pair", str(e.exception))
        with self.assertRaises(kraken.KrakenError):
            kraken.parse_ohlc({"error": []})                          # no result
        with self.assertRaises(kraken.KrakenError):
            kraken.parse_ohlc({"error": [], "result": {"A": [C1], "B": [C1], "last": 1}})
        with self.assertRaises(kraken.KrakenError):
            kraken.parse_ohlc(["not", "a", "dict"])

    def test_url_and_since_filter(self):
        self.assertEqual(kraken.ohlc_url("ETHXBT", 60),
                         "https://api.kraken.com/0/public/OHLC?pair=ETHXBT&interval=60")
        self.assertIn("&since=1700003600", kraken.ohlc_url("ETHXBT", 60, 1700003600))
        rows = kraken.fetch_ohlc("ETHXBT", 60, since=1700000000, get=lambda url: reply(C1, C2, C3))
        self.assertEqual([r[0] for r in rows], [1700003600])        # C1 not newer, C3 forming
        with self.assertRaises(ValueError):
            kraken.fetch_ohlc("ETHXBT", 7, get=lambda url: reply(C1, C2))


class TestCsv(unittest.TestCase):
    def test_read_csv(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "ETHXBT_60.csv")
            with open(p, "w") as f:
                f.write("1700003600,0.05,0.051,0.049,0.0505,10.5,7\n\n   \n"
                        "1700000000000,0.049,0.05,0.048,0.05,3,2\n")      # ms timestamp
            rows = kraken.read_csv(p)
            self.assertEqual(rows, [(1700000000, 0.049, 0.05, 0.048, 0.05, None, 3.0, 2),
                                    (1700003600, 0.05, 0.051, 0.049, 0.0505, None, 10.5, 7)])
            with open(p, "w") as f:
                f.write("1700000000,0.05,0.051,0.049,0.0505,10.5,7\n1700003600,oops,1,1,1,1,1\n")
            with self.assertRaises(ValueError) as e:
                kraken.read_csv(p)
            self.assertIn("line 2", str(e.exception))


class TestCandlesDb(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "candles.sqlite")
        self.conn = candles.connect(self.path)

    def tearDown(self):
        self.conn.close()
        self.tmp.cleanup()

    def test_insert_replaces_same_candle(self):
        rows = kraken.parse_ohlc(reply(C1, C2, C3))
        candles.insert(self.conn, "kraken", "ETHXBT", 60, rows)
        changed = [(1700003600, 100.5, 102.0, 100.0, 999.0, 101.0, 8.0, 25)]
        candles.insert(self.conn, "kraken", "ETHXBT", 60, changed)
        self.assertEqual(candles.summary(self.conn), [("kraken", "ETHXBT", 60, 2, 1700000000, 1700003600)])
        self.assertEqual(candles.last_ts(self.conn, "kraken", "ETHXBT", 60), 1700003600)
        self.assertIsNone(candles.last_ts(self.conn, "kraken", "ETHXBT", 1440))
        s = candles.load_close(self.path, "ETHXBT", 60)
        self.assertEqual(s.tolist(), [100.5, 999.0])
        self.assertEqual(str(s.index.tz), "UTC")
        self.assertTrue(candles.load_close(self.path, "NOPE", 60).empty)


class TestFetchCommand(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.tmp.name, "c.sqlite")

    def tearDown(self):
        self.tmp.cleanup()

    def run_main(self, argv, get):
        buf = io.StringIO()
        with redirect_stdout(buf):
            fetch.main(["--db", self.db, *argv], get=get, sleep=lambda s: None)
        return buf.getvalue()

    def test_incremental_fetch(self):
        api = FakeKraken({"ETHXBT": [C1, C2, C3]})
        out = self.run_main(["--pair", "ETHXBT", "--interval", "60"], api)
        self.assertIn("+2", out)
        self.assertNotIn("since=", api.urls[0])                      # first run: no since
        api.books["ETHXBT"] = [C2, C3, C4]                             # an hour later
        out = self.run_main(["--pair", "ETHXBT", "--interval", "60"], api)
        self.assertIn("since=1700003600", api.urls[1])                # asks from the newest stored
        self.assertIn("+1", out)                                       # only C3 is new and finished
        conn = candles.connect(self.db)
        self.assertEqual(candles.summary(conn), [("kraken", "ETHXBT", 60, 3, 1700000000, 1700007200)])
        conn.close()

    def test_one_failure_does_not_stop_the_rest(self):
        api = FakeKraken({"ETHXBT": [C1, C2], "XBTUSD": [C1, C2, C3]}, fail={"ETHXBT"})
        with mock.patch.object(config, "FETCH_PAIRS", ["ETHXBT", "XBTUSD"]):
            out = self.run_main(["--interval", "60"], api)
        self.assertIn("ETHXBT        60 min  FAILED: OSError: connection reset", out)
        self.assertIn("XBTUSD        60 min  +2", out)
        self.assertIn("1 fetch(es) failed", out)

    def test_import_and_list(self):
        p = os.path.join(self.tmp.name, "x.csv")
        with open(p, "w") as f:
            f.write("1700000000,0.05,0.051,0.049,0.0505,10.5,7\n")
        out = self.run_main(["--import", p, "--pair", "ETHXBT", "--interval", "1440"], None)
        self.assertIn("imported 1 candles: ETHXBT 1440 min", out)
        out = self.run_main(["--list"], None)
        self.assertIn("ETHXBT", out)
        self.assertIn("2023-11-14 22:13 UTC", out)


if __name__ == "__main__":
    unittest.main()
