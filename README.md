# Good Stocks

A free stock screener for **all US, EU and Czech listed stocks**, hosted on GitHub Pages.
It shows the metrics from the HelloStocks screener (revenue and earnings growth, ROE, debt to equity, free cash flow, PEG), and each stock gets an *x/7 criteria* score that you can tune yourself.

* **Website:** <https://web-tools-online.github.io/good-stocks/>
* **Database:** SQLite, published as a release asset: [`stocks.db`](https://github.com/web-tools-online/good-stocks/releases/download/data/stocks.db)
* **Updates:** automatically every Saturday (GitHub Actions)

## One-time setup

1. Merge this branch into `main`.
2. **Settings → Pages → Build and deployment → Source: _GitHub Actions_.**
3. **Actions → "Update stock data" → Run workflow** (or wait for Saturday). The first full run takes about an hour. After that the site is live at the address above.
4. *(Optional)* **Settings → Secrets and variables → Actions → Variables → New variable** `SEC_USER_AGENT` = `Your Name your@email.com`. The SEC asks automated clients to identify themselves. Without it a generic project identifier is sent.

## What is on the page

| Column | Definition |
| --- | --- |
| Revenue Growth (1Y, TTM) | Revenue of the last 12 months vs. the 12 months before. Marked `*` when it is estimated from the latest quarter vs. the same quarter a year earlier. |
| Earnings Growth (5Y) | Change in net income between the latest fiscal year and the fiscal year 5 years earlier. |
| Revenue Growth (5Y) | The same for revenue. |
| ROE | Return on equity: net income (TTM) ÷ shareholders' equity. |
| Debt to Equity | Total debt ÷ shareholders' equity (most recent quarter). |
| Free Cash Flow (TTM) | Operating cash flow − capital expenditure, last 12 months. |
| PEG (5Y Exp) | P/E ÷ analysts' expected yearly EPS growth over the next 5 years. |

The default strategy criteria are: revenue growth 1Y ≥ 5 %, earnings growth 5Y ≥ 50 %, revenue growth 5Y ≥ 50 %, ROE ≥ 15 %, debt/equity ≤ 1, FCF > 0 and 0 < PEG ≤ 2. Open **Show Strategy Criteria** to change thresholds or switch criteria off. Your settings are saved in your browser.

Other features:
* tabs for All / US / EU / Czech
* search, sector, country and market-cap filters
* values in USD, EUR, CZK or the local currency
* sorting by any column
* CSV download of the current view

**About 5-year growth:** Yahoo Finance only gives the last 4 fiscal years. US companies get older years from SEC EDGAR, so their 5-year figure is exact. For EU and Czech companies the database stores every annual report it sees, so their figures become exact as history builds up. Until then the 5-year figure is marked `≈`: the yearly growth rate over the 3–4 years available, extended to 5 years.

## How it works

```
Saturday 04:23 UTC  ──►  universe ──► fetch × 12 shards (parallel) ──► build ──► deploy
                          │             │                               │          │
   Nasdaq Trader lists ───┤             │ Yahoo Finance: profile,       │          └─► GitHub Pages
   Yahoo screener (EU/CZ) ┘             │ key stats, financial          │
                                        │ statements, PEG               ├─► SEC EDGAR (older US years)
                                        │                               ├─► ECB exchange rates
                                        │                               ├─► stocks.db  (release asset "data")
                                        │                               └─► site/data/stocks.json + stocks.csv
```

* **US**: every common stock and ADR on NASDAQ, NYSE, NYSE American, NYSE Arca and Cboe, from the [Nasdaq Trader symbol directory](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs). ETFs, warrants, units, preferred shares and notes are left out.
* **EU**: equities on the main exchange of each EU member state covered by Yahoo Finance (Xetra, Euronext Paris/Amsterdam/Brussels/Lisbon/Dublin, Borsa Italiana, BME, Wiener Börse, Nasdaq Nordic & Baltic, Warsaw, Athens, Budapest, Bucharest). Two rules keep the list clean:
  * Foreign companies traded on those exchanges, such as Apple on Xetra, are excluded.
  * A company listed on several EU exchanges is kept once, on its home exchange.
* **Czech**: all shares on the Prague Stock Exchange.

The database is never committed to git, so the repository stays small. Each run downloads the current `stocks.db` from the `data` release, updates it and uploads it again. A copy is also kept as a workflow artifact for 30 days. If a ticker fails to download, it keeps last week's numbers. A small `data/summary.json` is committed each week. That commit also stops GitHub from pausing the schedule, which it does after 60 days without repository activity.

Pushes to any branch other than `main` run a quick smoke test (15 tickers per market) and publish nothing. Site-only changes on `main` are deployed by the *Deploy website* workflow from the latest database, without a new data refresh.

## The database

| Table | Content |
| --- | --- |
| `listings` | One row per ticker: market, exchange, name, sector, industry, country, currency, status (`active` / `excluded` / `duplicate` / `delisted`) |
| `metrics` | Latest metrics per active ticker (growth rates and ROE as fractions, `*_usd` columns converted with ECB rates) |
| `financials` | Statement history (annual `A`, quarterly `Q`, trailing `T`) for revenue, net income, FCF, equity, debt… from Yahoo and SEC; grows every week |
| `history` | Weekly snapshot of the key metrics per ticker |
| `yahoo_stats` | Latest raw key statistics from Yahoo |
| `fx_rates`, `runs`, `meta` | Exchange rates, run log, last update time |

Example (with the `sqlite3` command-line tool or [DB Browser for SQLite](https://sqlitebrowser.org/)):

```sql
-- Czech stocks with ROE above 15 %
SELECT l.symbol, l.name, ROUND(m.roe * 100, 1) AS roe_pct, m.debt_to_equity, m.peg_5y
FROM listings l JOIN metrics m USING (symbol)
WHERE l.market = 'CZ' AND m.roe > 0.15
ORDER BY m.roe DESC;

-- Weekly ROE history of one company
SELECT week, roe FROM history WHERE symbol = 'CEZ.PR' ORDER BY week;
```

## Running locally

```bash
pip install -r requirements-dev.txt
pytest -q

# small end-to-end run (needs internet access)
python -m stockdb universe --limit 10 --shards 1
python -m stockdb fetch --shard 0 --out work/raw/raw-0.jsonl.gz
python -m stockdb build
python -m stockdb export
python -m http.server --directory _site 8000   # open http://localhost:8000
```

Code layout: `stockdb/` holds the Python pipeline:
* `universe.py`: ticker lists
* `yahoo.py`: Yahoo Finance client
* `sec.py`: SEC EDGAR history
* `metrics.py`: metric formulas
* `pipeline.py`: the universe, fetch and build steps
* `export.py`: JSON/CSV for the site

`site/` holds the static website, written in plain HTML, CSS and JavaScript with no build step.

## Limitations

Yahoo Finance is an unofficial data source. Coverage of small EU and Czech companies can be patchy, and some values may be missing or wrong. This project is for information only and is not investment advice.
