"""Insert fictional demo accounts explicitly; safe to rerun without duplicates."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings
from app.store import connect, initialize


def seed() -> None:
    path = get_settings().database_path
    initialize(path)
    today = datetime.now(ZoneInfo("Asia/Jakarta")).date()
    samples = [
        ("CF-1001", "Alex Morgan", 4_500_000, 104, "Sarah Wilson"),
        ("CF-1002", "Jordan Lee", 2_750_000, 67, "Daniel Brooks"),
        ("CF-1003", "Taylor Reed", 1_200_000, 42, "Sarah Wilson"),
        ("CF-1004", "Casey Parker", 3_600_000, 24, "Daniel Brooks"),
        ("CF-1005", "Riley Quinn", 850_000, 12, "Sarah Wilson"),
        ("CF-1006", "Jamie Blake", 5_000_000, 5, "Daniel Brooks"),
        ("CF-1007", "Avery Stone", 2_100_000, 0, "Sarah Wilson"),
        ("CF-1008", "Morgan Ellis", 1_500_000, -7, "Daniel Brooks"),
    ]
    with connect(path) as connection:
        before = connection.total_changes
        connection.executemany(
            "INSERT OR IGNORE INTO accounts VALUES (?, ?, ?, ?, ?, ?)",
            [(ref, name, f"LN-{ref[3:]}", amount, (today - timedelta(days=days)).isoformat(), owner)
             for ref, name, amount, days, owner in samples],
        )
        print(f"Inserted {connection.total_changes - before} fictional demo accounts.")


if __name__ == "__main__":
    seed()
