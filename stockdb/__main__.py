"""Command line entry point: ``python -m stockdb <command>``."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="stockdb", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("universe", help="build the list of tickers and split it into shards")
    p.add_argument("--db", default="work/stocks.db")
    p.add_argument("--out", default="work/universe.json")
    p.add_argument("--shards", type=int, default=8)
    p.add_argument("--limit", type=int, default=0, help="max listings per market (0 = all)")

    p = sub.add_parser("fetch", help="fetch Yahoo Finance data for one shard")
    p.add_argument("--universe", default="work/universe.json")
    p.add_argument("--shard", type=int, required=True)
    p.add_argument("--out", required=True, help="output .jsonl.gz")
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--rate", type=float, default=4.0, help="max requests per second")
    p.add_argument("--max-minutes", type=float, default=300)

    p = sub.add_parser("build", help="store the fetched data in the SQLite database")
    p.add_argument("--db", default="work/stocks.db")
    p.add_argument("--universe", default="work/universe.json")
    p.add_argument("--raw", nargs="+", default=["work/raw/*.jsonl.gz"])

    p = sub.add_parser("export", help="export the database to the static site")
    p.add_argument("--db", default="work/stocks.db")
    p.add_argument("--site", default="site")
    p.add_argument("--out", default="_site")
    p.add_argument("--summary", default=None, help="also write summary.json to this path")

    p = sub.add_parser("report", help="print a sample of the database")
    p.add_argument("--db", default="work/stocks.db")
    p.add_argument("--top", type=int, default=15, help="largest companies per market")

    p = sub.add_parser("inspect", help="print raw Yahoo Finance fields of some tickers (debugging)")
    p.add_argument("symbols", nargs="+")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("yfinance").setLevel(logging.CRITICAL)

    if args.command == "universe":
        from .pipeline import run_universe

        shards = run_universe(args.db, args.out, args.shards, args.limit)
        if os.environ.get("GITHUB_OUTPUT"):
            with open(os.environ["GITHUB_OUTPUT"], "a") as fh:
                fh.write(f"shards={json.dumps(shards)}\n")
    elif args.command == "fetch":
        from .pipeline import run_fetch

        run_fetch(args.universe, args.shard, args.out, args.workers, args.rate, args.max_minutes)
    elif args.command == "build":
        from .pipeline import run_build

        run_build(args.db, args.universe, args.raw)
    elif args.command == "export":
        from .export import run_export

        run_export(args.db, args.site, args.out, args.summary)
    elif args.command == "report":
        from .export import print_report

        print_report(args.db, args.top)
    elif args.command == "inspect":
        from .inspect import run_inspect

        run_inspect(args.symbols)
    return 0


if __name__ == "__main__":
    sys.exit(main())
