"""Seed demo Watchlist snapshot history for presentation charts."""

import argparse

from precision_app import repository, seed_demo_snapshots


def _select_user(identifier):
    if identifier:
        return repository.get_user_by_email(identifier) or repository.get_user_by_display_name(identifier) or repository.get_user_by_id(identifier)
    users = repository.list_users(limit=1)
    return users[0] if users else None


def _demo_watchlist_items(user_id):
    return [
        item
        for item in repository.list_watchlist_items(user_id=user_id, include_archived=False, limit=50)
        if (item.get("source_label") or item.get("data_source_label")) == "demo"
    ]


def main():
    parser = argparse.ArgumentParser(description="Seed demo snapshot history for Watchlist presentation charts.")
    parser.add_argument("--user", help="Display name, email, or id of the user who should own the demo Watchlist item.")
    parser.add_argument("--keyword", default="presentation demo product", help="Keyword for a demo Watchlist item if one must be created.")
    args = parser.parse_args()

    user = _select_user(args.user)
    if not user:
        raise SystemExit("No user found. Register or log in once, then run this script again.")

    items = _demo_watchlist_items(user["_id"])
    if not items:
        item_id, _created = repository.create_watchlist_item(
            user["_id"],
            {
                "keyword": args.keyword,
                "product_label": args.keyword,
                "tracking_mode": "search_scope",
                "platform_scope": "demo_all",
                "source_label": "demo",
                "data_source_label": "demo",
            },
        )
        items = [repository.get_watchlist_item(item_id, user["_id"])]

    created = seed_demo_snapshots([item["_id"] for item in items], user_id=user["_id"])
    print(f"Seeded {len(created)} demo snapshots for {len(items)} demo Watchlist item(s).")
    print("Open Watchlist and select the demo item to view its presentation trend chart.")


if __name__ == "__main__":
    main()
