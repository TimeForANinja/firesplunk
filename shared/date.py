from datetime import datetime, timedelta
from typing import List


def get_target_dates(last_n_days: int) -> List[str]:
    """Returns a list of date strings for the last N days (including today)."""
    now = datetime.now()
    return [
        (now - timedelta(days=i)).strftime('%Y-%m-%d')
        for i in range(last_n_days)
    ]
