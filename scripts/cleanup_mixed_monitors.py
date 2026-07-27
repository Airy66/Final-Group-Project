"""Audit (or explicitly delete) legacy query-upserted monitor test data.

Run with no flags for a dry run.  ``--delete`` only removes the affected
monitor, its snapshots, and its predictions; search runs and saved evidence
are never touched.
"""
import argparse

from precision_app import repository


parser = argparse.ArgumentParser()
parser.add_argument("--delete", action="store_true", help="delete monitors reported by the dry run")
args = parser.parse_args()
affected = repository.find_mixed_monitor_snapshots()
if not affected:
    print("No mixed monitor snapshots found.")
for row in affected:
    print(f"monitor={row['monitor_id']} snapshots={row['snapshot_count']} selected_groups={row['selected_groups']}")
if args.delete:
    for row in affected:
        repository.delete_monitor_with_history(row["watchlist_id"])
    print(f"Deleted {len(affected)} affected monitor(s) and their snapshot/prediction history.")
else:
    print("Dry run only. Re-run with --delete to remove the affected monitors.")
