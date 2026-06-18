from collections import defaultdict
from typing import List, Dict, Any, Tuple, Set, Optional
from pymongo.synchronous.database import Database

from shared.tasks import TaskType


def get_missing_count(db: Database, target_dates: List[str]) -> Tuple[int, Set[str]]:
    """Calculates missing days and returns (missing_count, present_dates)."""
    status_results = list(db['data_status'].find({'date': {'$in': target_dates}}))
    present_dates = {item['date'] for item in status_results}
    missing_count = len(target_dates) - len(present_dates)
    return missing_count, present_dates


def pad_timeline(timeline_results: Dict[str, int], present_dates: Set[str], target_dates: List[str]) -> List[Dict[str, Any]]:
    """Ensures all target dates are present in the timeline, with count 0 and has_data flag."""
    timeline = []
    for date_str in sorted(target_dates):
        timeline.append({
            'timestamp': date_str,
            'count': timeline_results.get(date_str, 0),
            'has_data': date_str in present_dates
        })
    return timeline


def sum_activity_counters(raw_hits: List[Dict[str, Any]], field_name: str, activity_fields: str = 'activity') -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """
    Sums up activity counters and calculates last activity for a list of hits.
    Returns a tuple (processed_hits, timeline_results).
    """
    timeline_results = defaultdict(int)
    processed_hits = []

    for item in raw_hits:
        combined_activity = {}
        activity = item.get(activity_fields, {})
        if not activity:
            continue

        for date, count in activity.items():
            combined_activity[date] = combined_activity.get(date, 0) + count
            timeline_results[date] += count

        total_hits = sum(combined_activity.values())
        last_activity = max(combined_activity.keys()) if combined_activity else None
        
        hit_data = {
            field_name: item.get(field_name),
            'count': total_hits,
            'last_activity': last_activity
        }
        processed_hits.append(hit_data)
    
    return processed_hits, dict(timeline_results)


def check_for_warnings(db: Database, target_dates: List[str], task_manager: Any) -> Optional[str]:
    """Checks for data gaps and task status to return a warning message if needed."""
    warning_parts = []

    missing_count, _ = get_missing_count(db, target_dates)
    if missing_count > 0:
        warning_parts.append(f"missing data for {missing_count}/{len(target_dates)} days")

    tasks = task_manager.get_tasks()
    if any(t['type'] == TaskType.BUILD_INDEX.value for t in tasks):
        warning_parts.append("index build ongoing - results may be incomplete")
    if any(t['type'] == TaskType.UPLOAD_DATA.value for t in tasks):
        warning_parts.append("data import ongoing - results may be incomplete")
    if any(t['type'] == TaskType.DELETE_DATE.value for t in tasks):
        warning_parts.append("deletion ongoing - results may be incomplete")

    return "; ".join(warning_parts) if warning_parts else None
