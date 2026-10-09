# Good Stocks

A free stock screener for **all US, EU and Czech listed stocks**, hosted on GitHub Pages.
It shows the metrics from the HelloStocks screener (revenue and earnings growth, ROE, debt to equity, free cash flow, PEG), and each stock gets an *x/7 criteria* score that you can tune yourself.

* **Website:** <https://web-tools-online.github.io/good-stocks/>
* **Database:** SQLite, published as a release asset: [`stocks.db`](https://github.com/web-tools-online/good-stocks/releases/download/data/stocks.db)
* **Updates:** automatically every Saturday (GitHub Actions)

## One-time setup

1. **Settings → Pages → Build and deployment → Source: _GitHub Actions_.** Do this first, because the update below publishes the site.
2. Merge this branch into `main`. This starts the first full data update automatically (about 30–60 minutes). After that the site is live at the address above.
3. If the first run's *deploy* job failed (for example because Pages wasn't enabled yet), start it again: **Actions → "Update stock data" → Run workflow**.

## What is on the page

| Column | Definition |
| --- | --- |
| Revenue Growth (1Y, TTM) | Revenue of the last 12 months vs. the 12 months before. Marked `*` when it is estimated from the latest quarter vs. the same quarter a year earlier. |
| Earnings Growth (4Y) | Change in net income across the last four fiscal years: the latest year vs. the oldest of the four (e.g. FY2022 → FY2025). |
| Revenue Growth (4Y) | The same for revenue. |
| ROE | Return on equity: net income (TTM) ÷ shareholders' equity. |
| Debt to Equity | Total debt ÷ shareholders' equity (most recent quarter). |
| Free Cash Flow (TTM) | Operating cash flow − capital expenditure, last 12 months. |
| PEG (5Y Exp) | P/E ÷ analysts' expected yearly EPS growth over the next 5 years. |
| P/E (TTM) | Share price ÷ earnings per share of the last 12 months. |
| Dividend Yield | Expected dividends over the next 12 months ÷ share price. |

**Data checks:** Yahoo's per-share figures (dividend per share, EPS) are occasionally broken, usually right after a reverse split. For example, GMEX showed a $453.60 dividend on a $1.94 share, and a P/E of 0.0007 despite a net loss. They are checked against company totals from the financial statements:
* A dividend larger than the share price is hidden.
* A yield of 15 % or more must be confirmed by the dividends actually paid. Otherwise the paid amount ÷ market cap is shown, marked `*`.
* A P/E of a loss-making company, or one more than 10× off market cap ÷ net income, is hidden.

Ordinary values are left as Yahoo reports them. On a sample of 62 dividend payers none of them changed. Run **Actions → "Inspect tickers"** to see the raw Yahoo data behind any ticker.

The default strategy criteria are: revenue growth 1Y ≥ 5 %, earnings growth 4Y ≥ 30 %, revenue growth 4Y ≥ 30 %, ROE ≥ 15 %, debt/equity ≤ 1, FCF > 0 and 0 < PEG ≤ 2. A missing value counts as a fail, except for PEG: stocks without analysts' growth forecasts (common in Europe) are scored on the other six criteria, shown e.g. as 6/6. Open **Show Strategy Criteria** to change thresholds or switch criteria off. Your settings are saved in your browser.

Other features:
* tabs for All / US / EU / Czech
* search, sector, country and market-cap filters
* values in USD, EUR, CZK or the local currency
* sorting by any column
* CSV download of the current view

**Only current data is used:** each weekly run downloads the current fundamentals and replaces last week's numbers. The 4Y growth uses the four fiscal years included in Yahoo Finance's current statements.

## How it works

```
Saturday 04:23 UTC  ──►  universe ──► fetch × 12 shards (parallel) ──► build ──► deploy
                          │             │                               │          │
   Nasdaq Trader lists ───┤             │ Yahoo Finance: current        │          └─► GitHub Pages
   Yahoo screener (EU/CZ) ┘             │ profile, key stats,           ├─► ECB exchange rates
                                        │ statements, PEG               ├─► stocks.db  (release asset "data")
                                        │                               └─► site/data/stocks.json + stocks.csv
```

* **US**: every common stock and ADR on NASDAQ, NYSE, NYSE American, NYSE Arca and Cboe, from the [Nasdaq Trader symbol directory](https://www.nasdaqtrader.com/trader.aspx?id=symboldirdefs). ETFs, warrants, units, preferred shares and notes are left out.
* **EU**: equities on the main exchange of each EU member state covered by Yahoo Finance (Xetra, Euronext Paris/Amsterdam/Brussels/Lisbon/Dublin, Borsa Italiana, BME, Wiener Börse, Nasdaq Nordic & Baltic, Warsaw, Athens, Budapest, Bucharest). Two rules keep the list clean:
  * Foreign companies traded on those exchanges, such as Apple on Xetra, are excluded.
  * A company listed on several EU exchanges is kept once, on its home exchange.
* **Czech**: all shares traded on the Prague Stock Exchange, including foreign companies listed there (e.g. Erste, Deutsche Telekom).

The database is never committed to git, so the repository stays small. Each run downloads the current `stocks.db` from the `data` release, overwrites last week's numbers with the current ones and uploads it again. It keeps no history. A copy is also kept as a workflow artifact for 30 days. If a ticker fails to download, it keeps last week's numbers. A small `data/summary.json` is committed each week. That commit also stops GitHub from pausing the schedule, which it does after 60 days without repository activity.

Pushes to any branch other than `main` run a quick smoke test (15 tickers per market) and publish nothing. Site-only changes on `main` are deployed by the *Deploy website* workflow from the latest database, without a new data refresh.

## The database

| Table | Content |
| --- | --- |
| `listings` | One row per ticker: market, exchange, name, sector, industry, country, currency, status (`active` / `excluded` / `duplicate` / `delisted`) |
| `metrics` | Current metrics per active ticker (growth rates and ROE as fractions, `*_usd` columns converted with ECB rates) |
| `fx_rates`, `runs`, `meta` | Current exchange rates, run log, last update time |

Example (with the `sqlite3` command-line tool or [DB Browser for SQLite](https://sqlitebrowser.org/)):

```sql
-- Czech stocks with ROE above 15 %
SELECT l.symbol, l.name, ROUND(m.roe * 100, 1) AS roe_pct, m.debt_to_equity, m.peg_5y
FROM listings l JOIN metrics m USING (symbol)
WHERE l.market = 'CZ' AND m.roe > 0.15
ORDER BY m.roe DESC;

-- US stocks passing the main quality checks
SELECT l.symbol, l.name, m.rev_growth_1y, m.roe, m.debt_to_equity, m.fcf_ttm_usd, m.peg_5y
FROM listings l JOIN metrics m USING (symbol)
WHERE l.market = 'US' AND m.roe >= 0.15 AND m.debt_to_equity BETWEEN 0 AND 1
  AND m.fcf_ttm_usd > 0 AND m.peg_5y BETWEEN 0 AND 2
ORDER BY m.market_cap_usd DESC;
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
* `metrics.py`: metric formulas
* `pipeline.py`: the universe, fetch and build steps
* `export.py`: JSON/CSV for the site

`site/` holds the static website, written in plain HTML, CSS and JavaScript with no build step.

## Limitations

Yahoo Finance is an unofficial data source. Coverage of small EU and Czech companies can be patchy, and some values may be missing or wrong. This project is for information only and is not investment advice.
