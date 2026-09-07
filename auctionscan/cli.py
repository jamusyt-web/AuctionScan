"""Command line entry point.

    # Screenshot a listing you're looking at, add it to your watchlist
    python -m auctionscan grab shot1.png shot2.png --store watchlist.csv

    # Score everything you've collected
    python -m auctionscan scan --csv watchlist.csv --valuation csv

    python -m auctionscan vin 1FTFW1E85MFA12345
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import Config
from .enrich.history import CarfaxHistory, NmvtisHistory, NullHistory
from .enrich.valuation import (
    AccuTradeValuation, CsvValuation, HeuristicValuation, MmrValuation,
)
from .enrich.vin import decode_vin
from .pipeline import run
from .report import to_console, to_csv, to_html
from .sources.csv_source import CsvSource

VALUATIONS = {
    "csv": CsvValuation,
    "heuristic": HeuristicValuation,
    "mmr": MmrValuation,
    "accutrade": AccuTradeValuation,
}
HISTORIES = {"none": NullHistory, "nmvtis": NmvtisHistory, "carfax": CarfaxHistory}


def cmd_scan(args: argparse.Namespace) -> int:
    cfg = Config.load(args.config) if args.config else Config()

    if not args.csv:
        print("Provide --csv with a run list export.", file=sys.stderr)
        return 2

    listings = list(CsvSource(args.csv, source_label=args.source_label))
    print(f"Loaded {len(listings)} listing(s) from {args.csv}", file=sys.stderr)

    try:
        valuation = VALUATIONS[args.valuation]()
    except Exception as exc:  # noqa: BLE001 - surface config problems plainly
        print(f"Valuation source '{args.valuation}' unavailable: {exc}", file=sys.stderr)
        return 1

    try:
        history = HISTORIES[args.history]()
    except Exception as exc:  # noqa: BLE001
        print(f"History source '{args.history}' unavailable: {exc}", file=sys.stderr)
        return 1

    analyzer = None
    if args.ai and cfg.use_ai_condition:
        from .ai.condition import ConditionAnalyzer

        analyzer = ConditionAnalyzer(
            model=cfg.model,
            max_photos=cfg.max_photos_per_vehicle,
            effort=args.effort,
        )

    deals, dropped, data_issues = run(
        listings,
        valuation=valuation,
        cfg=cfg,
        history=history,
        analyzer=analyzer,
        decode=args.decode,
        transport_miles=args.transport_miles,
    )

    # Data problems are always loud: a mistyped or misread VIN means a real
    # car is missing from the list, and a silent drop hides that.
    if data_issues:
        print(f"\n{len(data_issues)} listing(s) could not be read:", file=sys.stderr)
        for issue in data_issues:
            print(f"  ! {issue}", file=sys.stderr)
        print("", file=sys.stderr)

    if args.executable_only:
        before = len(deals)
        deals = [d for d in deals if d.executable]
        print(f"Executable only: kept {len(deals)} of {before}", file=sys.stderr)

    if args.formats:
        wanted = {f.strip().lower() for f in args.formats.split(",") if f.strip()}
        deals = [
            d for d in deals
            if d.acquisition and d.acquisition.format.value in wanted
        ]
        print(f"Format filter {sorted(wanted)}: {len(deals)} left", file=sys.stderr)

    if dropped and args.verbose:
        print(f"\nFiltered out {len(dropped)}:", file=sys.stderr)
        for note in dropped:
            print(f"  - {note}", file=sys.stderr)

    print(to_console(deals, limit=args.limit))

    if args.out_csv:
        to_csv(deals, args.out_csv)
        print(f"Wrote {args.out_csv}", file=sys.stderr)
    if args.out_html:
        to_html(deals, args.out_html)
        print(f"Wrote {args.out_html}", file=sys.stderr)

    if valuation.name == "heuristic":
        print(
            "\nNOTE: values came from the offline estimator, not a market source. "
            "Wire up MMR or AccuTrade before bidding real money.",
            file=sys.stderr,
        )
    return 0


def cmd_grab(args: argparse.Namespace) -> int:
    """Read listings out of screenshots and append them to a watchlist CSV."""
    import csv as _csv

    from .ai.extract import CSV_FIELDS, ScreenshotExtractor, to_csv_row
    from .enrich.vin import validate_vin

    paths = [Path(p) for p in args.images]
    missing = [p for p in paths if not p.exists()]
    if missing:
        print(f"Not found: {', '.join(str(m) for m in missing)}", file=sys.stderr)
        return 2

    extractor = ScreenshotExtractor(model=args.model, effort=args.effort)

    # --separate treats each image as its own car; the default merges every
    # screenshot into one listing (vehicle page + AccuTrade panel + Carfax).
    groups = [[p] for p in paths] if args.separate else [paths]

    rows = []
    for group in groups:
        names = ", ".join(p.name for p in group)
        print(f"Reading {names} ...", file=sys.stderr)
        try:
            extracted = extractor.extract(group, extra_text=args.text)
        except Exception as exc:  # noqa: BLE001 - report and keep going
            print(f"  failed: {exc}", file=sys.stderr)
            continue

        row = to_csv_row(extracted)
        vin = row["VIN"]
        if not vin:
            print("  no VIN found -- skipping", file=sys.stderr)
            continue
        if not validate_vin(vin):
            print(f"  WARNING: {vin} fails its check digit; verify by hand",
                  file=sys.stderr)
        label = " ".join(
            str(row[k]) for k in ("Year", "Make", "Model", "Trim") if row[k]
        )
        price = row["Buy Now"] or row["Reserve"] or row["Current Bid"] or "?"
        print(f"  {vin}  {label}  {row['Sale Format'] or 'timed'} @ {price}",
              file=sys.stderr)
        if extracted.notes:
            print(f"  note: {extracted.notes}", file=sys.stderr)
        rows.append(row)

    if not rows:
        print("Nothing extracted.", file=sys.stderr)
        return 1

    store = Path(args.store)
    existing: list[dict] = []
    if store.exists():
        with store.open(newline="", encoding="utf-8-sig") as fh:
            existing = list(_csv.DictReader(fh))

    # Re-grabbing a car you already have updates it rather than duplicating.
    by_vin = {r.get("VIN", ""): r for r in existing}
    for row in rows:
        by_vin[row["VIN"]] = row

    with store.open("w", newline="", encoding="utf-8") as fh:
        writer = _csv.DictWriter(fh, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for row in by_vin.values():
            writer.writerow({k: row.get(k, "") for k in CSV_FIELDS})

    print(f"\n{len(rows)} listing(s) -> {store} ({len(by_vin)} total)",
          file=sys.stderr)
    print(f"Now run: python -m auctionscan scan --csv {store} --valuation csv",
          file=sys.stderr)
    return 0


def cmd_vin(args: argparse.Namespace) -> int:
    decoded = decode_vin(args.vin)
    if decoded.errors:
        print("\n".join(decoded.errors), file=sys.stderr)
        return 1
    for field in ("vin", "year", "make", "model", "trim", "body_class",
                  "drive_type", "engine", "fuel_type", "plant_country"):
        value = getattr(decoded, field)
        if value:
            print(f"{field.replace('_', ' ').title():<16} {value}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="auctionscan",
        description="Find wholesale buys with enough meat to flip to another dealer.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Score a run list")
    scan.add_argument("--csv", type=Path, help="Run list CSV export")
    scan.add_argument("--source-label", default="csv", help="Marketplace name for the report")
    scan.add_argument("--config", type=Path, help="YAML config (buy box + costs)")
    scan.add_argument("--valuation", choices=list(VALUATIONS), default="heuristic")
    scan.add_argument("--history", choices=list(HISTORIES), default="none")
    scan.add_argument("--ai", action="store_true", help="Run AI condition analysis")
    scan.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                      default="medium", help="AI effort level")
    scan.add_argument("--decode", action="store_true", help="Decode VINs via NHTSA")
    scan.add_argument("--transport-miles", type=int, default=None)
    scan.add_argument("--limit", type=int, default=25)
    scan.add_argument(
        "--executable-only", action="store_true",
        help="Only cars you can buy at a known price now (Buy Now, Make Offer, "
             "no-reserve, or reserve already met). Hides timed lots whose "
             "reserve is hidden.",
    )
    scan.add_argument(
        "--format", dest="formats", default="",
        help="Comma-separated sale formats to keep: buy_now,make_offer,"
             "absolute,timed,live",
    )
    scan.add_argument("--out-csv", type=Path)
    scan.add_argument("--out-html", type=Path)
    scan.add_argument("-v", "--verbose", action="store_true")
    scan.set_defaults(func=cmd_scan)

    grab = sub.add_parser(
        "grab", help="Read a listing out of screenshots into your watchlist")
    grab.add_argument("images", nargs="+", help="Screenshot(s) of ONE vehicle")
    grab.add_argument("--store", type=Path, default=Path("watchlist.csv"),
                      help="Watchlist CSV to append to (default watchlist.csv)")
    grab.add_argument("--separate", action="store_true",
                      help="Treat each image as a different car")
    grab.add_argument("--text", default="",
                      help="Extra text pasted from the page")
    grab.add_argument("--model", default="claude-opus-5")
    grab.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"],
                      default="high")
    grab.set_defaults(func=cmd_grab)

    vin = sub.add_parser("vin", help="Decode a VIN via NHTSA vPIC")
    vin.add_argument("vin")
    vin.set_defaults(func=cmd_vin)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
