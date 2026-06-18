import time
import io
import csv
import sys
from typing import Tuple, Dict, List
from collections import defaultdict
from datetime import datetime, timedelta

import gridfs
from bson import ObjectId
from pymongo import UpdateOne, InsertOne

from shared.date import get_target_dates
from shared.tasks import TaskState
from shared.env import get_last_n_days
from .base import BaseTask


UPLOAD_BATCH_SIZE = 50_000
EXPIRE_GRACE_PERIOD = 2


# increase csv field size limit, since we've been hitting a max field size error
csv.field_size_limit(sys.maxsize)


class UploadDataTask(BaseTask):
    def run(self) -> Tuple[TaskState, str]:
        fs = gridfs.GridFS(self.db)
        start_time = time.time()

        file_id = self.data['file_id']
        if isinstance(file_id, str):
            file_id = ObjectId(file_id)
        filename = self.data['filename']

        self.update_progress(0, f"Reading {filename}")
        grid_out = fs.get(file_id)

        total_count = process_upload_stream(grid_out, self.db, lambda x, y, **kwargs: self._progress_callback(x, start_time, y, **kwargs))
        fs.delete(file_id)

        self.update_progress(100, "Done")
        elapsed = time.time() - start_time
        return TaskState.DONE, f"Processed {total_count} records from {filename} in {int(elapsed/60)}min"

    def _progress_callback(self, p: int, start_time: float, extra: str = "", records_per_sec: float = 0):
        info = f"{p}%"
        elapsed = time.time() - start_time
        if elapsed > 60:
            info += f" - {int(elapsed/60)}m elapsed"
        else:
            info += f" - {int(elapsed)}s elapsed"
        
        if records_per_sec > 0:
            info += f" ({int(records_per_sec)} rec/s)"

        if extra:
            info += f" [{extra}]"
        self.update_progress(p, info)


def _validate_item(item, i: int, allowed_dates: List[str], upload_time: float):
    """Validates and parses a single CSV row."""
    # check date
    date_val = item.get('date')
    if not date_val:
        return None, f'Record {i} is missing date'
    if date_val not in allowed_dates:
        return None, f'Record {i} has invalid date: {date_val}'

    # validate/parse count
    try:
        item['count'] = int(item.get('count', 0))
    except (ValueError, TypeError):
        return None, f'Record {i} has invalid count: {item.get("count")}'

    # set metadata
    item['uploaded_at'] = upload_time

    # validate/parse ports
    try:
        ports_val = item.get('ports', '')
        if not ports_val:
            item['ports'] = []
        elif isinstance(ports_val, str) and ',' in ports_val:
            item['ports'] = [int(p) for p in ports_val.split(',')]
        elif isinstance(ports_val, str) and ':' in ports_val:
            item['ports'] = [int(p) for p in ports_val.split(':')]
        else:
            item['ports'] = [int(ports_val)]
    except (ValueError, TypeError):
        return None, f'Record {i} has invalid ports: {item.get("ports")}'

    return item, None


def process_upload_stream(grid_out, db, progress_callback=None):
    """
    Process an upload stream and store records in the database.
    This function is designed to be called from a background task.
    """
    stream = io.TextIOWrapper(grid_out, encoding='utf-8')
    reader = csv.DictReader(stream)

    upload_time = datetime.now()
    today_str = upload_time.strftime('%Y-%m-%d')
    last_n_days = get_last_n_days()
    days_with_data = [
        doc['date'] for doc in db['data_status'].find({'status': 'present'}, {'_id': 0, 'date': 1})
    ]
    allowed_days = [
        x for x in get_target_dates(last_n_days)
        # forbid today and days for which we already have data
        if x != today_str and x not in days_with_data
    ]

    total_count = 0
    date_count = defaultdict(int)
    start_time = time.time()

    batch = []
    for i, row in enumerate(reader):
        batch.append((i, row))
        if len(batch) >= UPLOAD_BATCH_SIZE:
            processed_count = _process_batch(db, batch, allowed_days, upload_time, date_count)
            total_count += processed_count
            batch = []

            if progress_callback:
                elapsed = time.time() - start_time
                rps = total_count / elapsed if elapsed > 0 else 0
                progress_callback(50, f"Processed {i + 1} records...", records_per_sec=rps)

    # we have some left-overs that should still be processed
    if batch:
        processed_count = _process_batch(db, batch, allowed_days, upload_time, date_count)
        total_count += processed_count

    # Update data_status for each date processed
    for date_str, count in date_count.items():
        if date_str not in allowed_days:
            continue
        db['data_status'].update_one(
            {'date': date_str},
            {
                '$set': {
                    'count': count,
                    'uploaded_at': upload_time,
                    'status': 'present'
                }
            },
            upsert=True
        )

    if progress_callback:
        progress_callback(100, f"Upload complete. {total_count} records processed.", records_per_sec=0)
    
    return total_count


def _process_batch(db, current_items: List[Tuple[int, Dict]], allowed_dates: List[str], upload_time, date_count) -> int:
    # Validate items
    results = [
        _validate_item(x[1], x[0], allowed_dates, upload_time)
        for x in current_items
    ]
    valid_items = [r[0] for r in results if r[0] is not None]
    if not valid_items:
        return 0

    summaries_ops = []
    correlation_ips_ops = []
    correlation_ports_ops = []

    for item in valid_items:
        # track date count
        date_count[item['date']] += item['count']

        # summaries insert
        summaries_ops.append(InsertOne({
            'src_ip': item['src_ip'],
            'dest_ip': item['dest_ip'],
            'rule': item['rule'],
            'date': item['date'],
            'count': item['count'],
            'ports': item['ports'],
            'uploaded_at': item['uploaded_at']
        }))

        # IP correlations - Source
        correlation_ips_ops.append(UpdateOne(
            {'ip': item['src_ip'], 'rule': item['rule']},
            {
                '$inc': {
                    f'activity-src.{item["date"]}': item['count']
                },
            },
            upsert=True
        ))
        # IP correlations - Destination
        correlation_ips_ops.append(UpdateOne(
            {'ip': item['dest_ip'], 'rule': item['rule']},
            {
                '$inc': {
                    f'activity-dst.{item["date"]}': item['count']
                },
            },
            upsert=True
        ))
        # Port correlations
        for port in item['ports']:
            # TODO: this is inaccurate, since the count relates to a set of ports, not the individual ones
            correlation_ports_ops.append(UpdateOne(
                {'rule': item['rule'], 'port': port},
                {
                    '$inc': {f'activity.{item["date"]}': item['count']},
                },
                upsert=True
            ))

    # execute queries
    if summaries_ops:
        db['summaries'].bulk_write(summaries_ops, ordered=False)
    if correlation_ips_ops:
        db['correlated_rule_ip'].bulk_write(correlation_ips_ops, ordered=False)
    if correlation_ports_ops:
        db['correlated_rule_ports'].bulk_write(correlation_ports_ops, ordered=False)

    return len(valid_items)
