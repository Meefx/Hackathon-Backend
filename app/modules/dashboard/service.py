def summarize(accounts: list[dict]) -> dict:
    overdue = [account for account in accounts if account["days_past_due"] > 0]
    return {
        "total_accounts": len(accounts),
        "total_outstanding": sum(account["outstanding_amount"] for account in accounts),
        "overdue_accounts": len(overdue),
        "overdue_amount": sum(account["outstanding_amount"] for account in overdue),
        "buckets": [
            {
                "status": status,
                "count": sum(account["status"] == status for account in accounts),
                "outstanding_amount": sum(
                    account["outstanding_amount"] for account in accounts
                    if account["status"] == status
                ),
            }
            for status in ("current", "early", "late", "critical")
        ],
    }
