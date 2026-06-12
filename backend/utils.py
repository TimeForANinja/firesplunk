import csv
import io
import logging
import os
from collections import defaultdict
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from pymongo import UpdateOne

UPLOAD_BATCH_SIZE = 1_000
EXPIRE_GRACE_PERIOD = 2

def get_target_dates(last_n_days):
    """Returns a list of date strings for the last N days (including today)."""
    now = datetime.now()
    return [
        (now - timedelta(days=i)).strftime('%Y-%m-%d')
        for i in range(last_n_days)
    ]

def get_missing_count(db, target_dates):
    """Calculates missing days and returns (missing_count, present_dates)."""
    status_results = list(db['data_status'].find({'date': {'$in': target_dates}}))
    present_dates = {item['date'] for item in status_results}
    missing_count = len(target_dates) - len(present_dates)
    return missing_count, present_dates

def pad_timeline(timeline_results, present_dates, target_dates):
    """Ensures all target dates are present in the timeline, with count 0 and has_data flag."""
    timeline = []
    for date_str in sorted(target_dates):
        timeline.append({
            'timestamp': date_str,
            'count': timeline_results.get(date_str, 0),
            'has_data': date_str in present_dates
        })
    return timeline

def validate_item(item, i, today_str, upload_time, expires_at):
    """Validates and parses a single CSV row."""
    # check date
    date_val = item.get('date')
    if not date_val:
        return None, f'Record {i} is missing date'
    if date_val == today_str:
        # skip today
        return None, "Data for the current day is not supported"
    
    # validate/parse count
    try:
        item['count'] = int(item.get('count', 0))
    except (ValueError, TypeError):
        return None, f'Record {i} has invalid count: {item["count"]}'
    
    # set metadata
    item['uploaded_at'] = upload_time
    item['expires_at'] = expires_at

    # validate/parse ports
    try:
        ports_val = item.get('ports', '')
        item['ports'] = [] if not ports_val else [int(p) for p in ports_val.split(':')]
    except (ValueError, TypeError):
        return None, f'Record {i} has invalid ports: {item["ports"]}'
        
    return item, None

def process_upload_stream(stream_or_file, db, progress_callback=None):
    """
    Process an upload stream and store records in the database.
    This function is designed to be called from a background task.
    """
    if hasattr(stream_or_file, 'read'):
        # It's a file-like object (e.g., from GridFS)
        if hasattr(stream_or_file, 'encoding'):
            stream = stream_or_file
        else:
            stream = io.TextIOWrapper(stream_or_file, encoding='utf-8')
    else:
        # It's already a stream
        stream = stream_or_file

    reader = csv.DictReader(stream)

    upload_time = datetime.now()
    today_str = upload_time.strftime('%Y-%m-%d')
    # Use a default if LAST_N_DAYS is not available (though it should be in env)
    last_n_days = int(os.environ.get('APP_LAST_N_DAYS', 30))
    expires_at = upload_time + timedelta(days=last_n_days + EXPIRE_GRACE_PERIOD)

    total_count = 0
    date_count = defaultdict(int)

    def process_batch(current_items):
        nonlocal total_count
        
        with ThreadPoolExecutor() as executor:
            # Validate items in parallel
            results = list(executor.map(
                lambda x: validate_item(x[1], x[0], today_str, upload_time, expires_at),
                current_items
            ))
        
        valid_items = [r[0] for r in results if r[0] is not None]
        
        if valid_items:
            current_batch = []
            for item in valid_items:
                date_str = item['date']
                date_count[date_str] += 1
                
                current_batch.append(UpdateOne(
                    {
                        'src_ip': item['src_ip'],
                        'dest_ip': item['dest_ip'],
                        'rule': item['rule'],
                        'date': item['date']
                    },
                    {
                        '$set': {
                            'count': item['count'],
                            'ports': item['ports'],
                            'uploaded_at': item['uploaded_at'],
                            'expires_at': item['expires_at']
                        }
                    },
                    upsert=True
                ))
            
            # Divide into sub-batches for parallel writes
            sub_batch_size = max(1, len(current_batch) // 4)
            sub_batches = [current_batch[i:i + sub_batch_size] for i in range(0, len(current_batch), sub_batch_size)]
            
            with ThreadPoolExecutor() as executor:
                executor.map(lambda b: db['summaries'].bulk_write(b, ordered=False), sub_batches)
            
            total_count += len(valid_items)

    batch = []
    for i, row in enumerate(reader):
        batch.append((i, row))
        if len(batch) >= UPLOAD_BATCH_SIZE:
            process_batch(batch)
            batch = []
            if progress_callback:
                progress_callback(50, f"Processed {i+1} records...")

    if batch:
        process_batch(batch)

    # Update data_status for each date processed
    for date_str, count in date_count.items():
        db['data_status'].update_one(
            {'date': date_str},
            {
                '$set': {
                    'count': count,
                    'uploaded_at': upload_time
                }
            },
            upsert=True
        )

    if progress_callback:
        progress_callback(100, f"Upload complete. {total_count} records processed.")
