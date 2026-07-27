"""Run due Precision Curator Watchlist Monitor refreshes once per SGT day."""

import argparse
from datetime import date


def _scheduled_date(value):
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise argparse.ArgumentTypeError("scheduled date must use YYYY-MM-DD") from exc


def build_parser():
    parser = argparse.ArgumentParser(description="Refresh due daily Watchlist Monitors.")
    parser.add_argument("--dry-run", action="store_true", help="List due Monitor IDs and owners without calls or mutation.")
    parser.add_argument("--limit", type=int, help="Maximum Monitors processed in this run.")
    parser.add_argument("--monitor-id", help="Target one Monitor for controlled testing.")
    parser.add_argument("--force", action="store_true", help="Development/administrator test: bypass the due-time check.")
    parser.add_argument("--scheduled-date", type=_scheduled_date, help="Deterministic Singapore date in YYYY-MM-DD form.")
    return parser


def run(argv=None):
    args = build_parser().parse_args(argv)
    import precision_app

    configured_limit = int(precision_app.app.config.get("MONITOR_SCHEDULED_RUN_LIMIT", 5))
    limit = configured_limit if args.limit is None else max(0, min(args.limit, configured_limit))
    candidates = precision_app.due_monitor_candidates(monitor_id=args.monitor_id, force=args.force)[:limit]
    if args.dry_run:
        print(f"Due: {len(candidates)}")
        for monitor in candidates:
            print(f"Monitor: {monitor.get('_id')} Owner: {monitor.get('user_id')}")
        print("Dry run: no provider calls or database mutation.")
        return 0
    if not precision_app.app.config.get("MONITOR_DAILY_REFRESH_ENABLED", False):
        print("Scheduled Monitor refresh is disabled.")
        print("Due: 0\nSuccess: 0\nPartial: 0\nFailed: 0\nSkipped: 0")
        return 0

    counts = {"success": 0, "partial": 0, "failed": 0, "skipped": 0}
    for monitor in candidates:
        result = precision_app.refresh_monitor(
            monitor["_id"],
            monitor.get("user_id"),
            trigger="scheduled",
            force=args.force,
            scheduled_date=args.scheduled_date,
        )
        counts[result["status"]] += 1
    print(f"Due: {len(candidates)}")
    print(f"Success: {counts['success']}")
    print(f"Partial: {counts['partial']}")
    print(f"Failed: {counts['failed']}")
    print(f"Skipped: {counts['skipped']}")
    return 0


def main():
    raise SystemExit(run())


if __name__ == "__main__":
    main()
