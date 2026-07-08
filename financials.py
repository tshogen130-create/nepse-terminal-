"""Pipeline financials scraper — runs on GitHub Actions daily.
Pulls quarterly Revenue / Profit / Equity / Debt (+ reported EPS for banks) per
company from ShareSansar and MERGES into docs/data/financials.json: a symbol
that fails today keeps yesterday's numbers, so data never regresses.
Symbols it can't parse are listed under "_review" in the output JSON.
"""
import json, re, sys, time, datetime as dt
from pathlib import Path
import requests
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
OUT  = ROOT / "financials.json"
COMPANIES = json.loads((ROOT / "companies.json").read_text())

HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                         "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
           "Accept-Language": "en-US,en;q=0.9",
           "X-Requested-With": "XMLHttpRequest"}

LABEL_MAP = [
    (("net profit", "profit for the period", "profit/(loss)", "net income"), "profit"),
    (("total revenue", "revenue from operation", "net interest income",
      "total operating income", "gross premium", "net premium", "total income",
      "revenue"), "revenue"),
    (("total equity", "shareholder", "net worth", "equity attributable"), "equity"),
    (("total debt", "total borrowing", "debenture and borrowing", "long term loan",
      "borrowings"), "total_debt"),
    (("book value", "net worth per share"), "book_value"),
    (("earning per share", "earnings per share", "eps"), "eps"),
]
NUM = lambda s: (float(re.sub(r"[,()\s]", "", s)) * (-1 if "(" in s else 1)
                 if s and re.search(r"\d", s) else None)


def company_id(sess, symbol):
    r = sess.get(f"https://www.sharesansar.com/company/{symbol.lower()}",
                 headers=HEADERS, timeout=30)
    if r.status_code != 200:
        return None
    m = (re.search(r'id="company_id"[^>]*value="(\d+)"', r.text)
         or re.search(r"company[_-]?id['\"]?\s*[:=]\s*['\"]?(\d+)", r.text))
    return m.group(1) if m else None


def quarterly(sess, cid):
    r = sess.get("https://www.sharesansar.com/company-financial",
                 params={"id": cid, "financial_type": "quarterly_report"},
                 headers=HEADERS, timeout=30)
    if r.status_code != 200 or "<table" not in r.text:
        return None
    table = BeautifulSoup(r.text, "lxml").find("table")
    out, prev = {}, {}
    for tr in table.find_all("tr"):
        cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        label = cells[0].lower()
        latest = NUM(cells[1])
        earlier = NUM(cells[2]) if len(cells) > 2 else None
        for frags, field in LABEL_MAP:
            if any(f in label for f in frags) and field not in out and latest is not None:
                out[field] = latest
                if earlier is not None:
                    prev[field] = earlier
                break
    if "profit" in prev:
        out["previous_ytd_profit"] = prev["profit"]
    if "eps" in prev:
        out["prev_eps"] = prev["eps"]
    return out or None


def main():
    existing = {}
    if OUT.exists():
        try:
            existing = json.loads(OUT.read_text()).get("financials", {})
        except json.JSONDecodeError:
            pass

    only = [s.upper() for s in sys.argv[1:]]
    targets = [c["s"] for c in COMPANIES if not only or c["s"] in only]

    sess, fresh, review = requests.Session(), {}, []
    for i, sym in enumerate(targets, 1):
        data = None
        for attempt in (1, 2):
            try:
                cid = company_id(sess, sym)
                data = quarterly(sess, cid) if cid else None
                break
            except requests.RequestException as e:
                print(f"  {sym}: attempt {attempt} error {e}")
                time.sleep(3)
        if data and {"profit", "revenue"} & data.keys():
            data["_fetched"] = dt.date.today().isoformat()
            fresh[sym] = data
            print(f"[{i}/{len(targets)}] {sym}: ok ({len(data)} fields)")
        else:
            review.append(sym)
            print(f"[{i}/{len(targets)}] {sym}: review")
        time.sleep(1.2)

    merged = dict(existing)
    merged.update(fresh)                       # fresh wins; failures keep old values
    OUT.write_text(json.dumps({
        "asof": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "financials": merged,
        "_review": sorted(review)}, separators=(",", ":")))
    print(f"\n{len(fresh)} refreshed, {len(merged)} total, {len(review)} for review")


if __name__ == "__main__":
    main()
