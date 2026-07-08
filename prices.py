"""Pipeline price scraper — runs on GitHub Actions every 15 min during market hours.
Writes docs/data/prices.json. Sources: MeroLagani, ShareSansar fallback."""
import json, re, sys, datetime as dt
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
OUT  = ROOT / "prices.json"
SYMS = {c["s"] for c in json.loads((ROOT / "companies.json").read_text())}

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Accept-Language": "en-US,en;q=0.9"}
NUM = lambda s: (float(re.sub(r"[,\s%]", "", s)) if s and re.search(r"\d", s) else None)


def merolagani(sess):
    r = sess.get("https://merolagani.com/LatestMarket.aspx", headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    # only the main market board: the table with the most >=7-cell rows.
    # Side widgets (Top Turnover etc.) have 3-cell rows and must be ignored.
    best, best_n = None, 0
    for t in soup.find_all("table"):
        n = sum(1 for tr in t.find_all("tr") if len(tr.find_all("td")) >= 7)
        if n > best_n:
            best, best_n = t, n
    out = {}
    if best is None or best_n < 20:
        return out
    for tr in best.find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 7:
            continue
        sym = tds[0].get_text(strip=True).upper()
        if sym not in SYMS:
            continue
        ltp = NUM(tds[1].get_text())
        if ltp is None or ltp <= 0 or ltp > 100000:   # sanity cap
            continue
        out[sym] = {"ltp": ltp, "chg": NUM(tds[2].get_text())}
    return out


def sharesansar(sess):
    r = sess.get("https://www.sharesansar.com/today-share-price", headers=HEADERS, timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "lxml")
    table = soup.find("table")
    head = [th.get_text(strip=True).lower() for th in table.find_all("th")]
    def col(*names):
        for n in names:
            for i, h in enumerate(head):
                if n in h:
                    return i
        return None
    cs, cl, cc = col("symbol"), col("ltp", "close"), col("diff %", "% change", "change")
    out = {}
    for tr in table.find_all("tr")[1:]:
        tds = tr.find_all("td")
        if not tds or cs is None or cl is None or len(tds) <= max(cs, cl):
            continue
        sym = tds[cs].get_text(strip=True).upper()
        if sym not in SYMS:
            continue
        ltp = NUM(tds[cl].get_text())
        if ltp is None:
            continue
        out[sym] = {"ltp": ltp,
                    "chg": NUM(tds[cc].get_text()) if cc is not None and len(tds) > cc else None}
    return out


def main():
    sess = requests.Session()
    for name, fn in (("merolagani", merolagani), ("sharesansar", sharesansar)):
        try:
            prices = fn(sess)
        except Exception as e:                                    # noqa: BLE001
            print(f"{name} failed: {e}")
            continue
        if len(prices) >= 20:
            OUT.write_text(json.dumps({
                "asof": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
                "source": name, "prices": prices}, separators=(",", ":")))
            print(f"{len(prices)} symbols via {name}")
            return
    sys.exit("all price sources failed")                          # non-zero -> visible in Actions


if __name__ == "__main__":
    main()
