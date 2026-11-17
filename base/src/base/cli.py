"""List or smoke-fetch registered sources. Storage writes require explicit --store."""

from __future__ import annotations

import argparse
import json

from base.datasources import FetchError, fetch_source, list_sources
from base.storage import LocalJSONStore, R2Store, run_pipeline, smoke_sources


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sources", nargs="*", help="Source names to fetch (default: list all)")
    parser.add_argument("--kind", choices=("events", "entities"), default=None)
    parser.add_argument("--limit", type=int, default=2, help="Sample rows per source (output only)")
    parser.add_argument("--store", choices=("local", "r2"), help="Explicitly persist data; omitted by default")
    parser.add_argument("--root", default="./data", help="Root folder with --store local")
    parser.add_argument("--smoke", action="store_true", help="Temporary scoped run; inspect and cleanup")
    args = parser.parse_args()
    names = args.sources or [spec.name for spec in list_sources(args.kind)]
    if args.smoke and not args.store:
        parser.error("--smoke requires --store local or r2")
    if args.store:
        store = LocalJSONStore(args.root) if args.store == "local" else R2Store()
        result = (smoke_sources(store, names) if args.smoke else run_pipeline(names, store=store))
        print(json.dumps(result, indent=2))
        if args.smoke and result["summary"]["errors"]:
            return 1
        return 0
    if not args.sources:
        for spec in list_sources(args.kind):
            print(f"{spec.name:15} {spec.kind:8} {spec.description}")
        return 0
    result = 0
    for name in names:
        try:
            items = fetch_source(name)
            print(f"{name}: {len(items)} records")
            for item in items[:args.limit]:
                print(f"  {(item.get('title') or item.get('name'))!s}")
        except FetchError as exc:
            result = 1
            print(f"{name}: ERROR {exc}")
    return result


if __name__ == "__main__":
    raise SystemExit(main())
