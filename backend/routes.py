import logging
import threading
from collections import defaultdict
from datetime import datetime, timedelta
from urllib.parse import quote, urljoin
import csv
import io
from concurrent.futures import ThreadPoolExecutor
from pymongo import UpdateOne

from db import build_correlations
from models import (
    MissingDataResponseSchema, UploadSchema, IPSearchResultSchema, RuleSearchResultSchema
)


UPLOAD_BATCH_SIZE = 1_000
EXPIRE_GRACE_PERIOD = 2


def get_missing_count(db, target_dates):
    """Calculate the number of missing dates in the given range."""
    status_results = list(db['data_status'].find({'date': {'$in': target_dates}, 'status': 'present'}))
    present_dates = {item['date'] for item in status_results}
    missing_count = len(set(target_dates) - present_dates)
    return missing_count, present_dates


def pad_timeline(timeline_results, present_dates, target_dates):
    """Ensure all days in range are present in timeline."""
    timeline = []
    for d in sorted(target_dates):
        count = timeline_results.get(d, 0)
        timeline.append({
            'timestamp': d,
            'count': count,
            'has_data': d in present_dates
        })
    return timeline


def validate_item(item, i, today_str, upload_time, expires_at):
    """Validate a single CSV record."""
    required_fields = ['src_ip', 'dest_ip', 'rule', 'count', 'date']
    
    # Check for missing fields
    missing = [
        f
        for f in required_fields
        if f not in item
        or item[f] is None
        or str(item[f]).strip() == ''
    ]
    if missing:
        return None, f'Record {i} is missing required fields: {", ".join(missing)}'

    date_str = item['date']
    if date_str == today_str:
        # ignore any data for "today"
        return None, None

    item['uploaded_at'] = upload_time
    item['expires_at'] = expires_at

    # Validate count is integer
    try:
        item['count'] = int(item['count'])
    except (ValueError, TypeError):
        return None, f'Record {i} has invalid count: {item["count"]}'

    # validate/parse ports
    try:
        ports_val = item.get('ports', '')
        item['ports'] = [] if not ports_val else [int(p) for p in ports_val.split(':')]
    except (ValueError, TypeError):
        return None, f'Record {i} has invalid ports: {item["ports"]}'
        
    return item, None


def register_routes(app):
    # read required vars from config
    last_n_days = int(app.config.get('LAST_N_DAYS', 30))
    splunk_server_url = app.config.get('SPLUNK_SERVER_URL', 'https://splunk.example.com')
    splunk_query_template = app.config.get('SPLUNK_QUERY_TEMPLATE', 'index=net-fw | stats count, values(dest_port) as ports by src_ip dest_ip rule')

    @app.get('/')
    def index():
        return app.send_static_file('index.html')

    @app.get('/health')
    def health_check():
        return {'status': 'ok'}

    @app.get('/summaries/missing')
    @app.output(MissingDataResponseSchema)
    def get_missing_data():
        """Get status of data for the last N days and Splunk queries for missing ones."""
        days_data = []
        now = datetime.now()
        today_str = now.strftime('%Y-%m-%d')
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        
        # Calculate all target dates
        target_dates = []
        for i in range(0, last_n_days + 1):
            target_date = today_start - timedelta(days=i)
            target_dates.append(target_date)

        date_strings = [d.strftime('%Y-%m-%d') for d in target_dates]

        # Fetch status from data_status collection
        status_results = {
            item['date']: item 
            for item in app.config['MONGO_DB']['data_status'].find({'date': {'$in': date_strings}})
        }

        for target_date in target_dates:
            date_str = target_date.strftime('%Y-%m-%d')
            status_entry = status_results.get(date_str, {})

            # build splunk query
            earliest = target_date.strftime('%m/%d/%Y:00:00:00')
            latest = target_date.strftime('%m/%d/%Y:23:59:59')
            query = f'earliest="{earliest}" latest="{latest}" {splunk_query_template}'
            splunk_link = f"{urljoin(splunk_server_url, '/en-US/app/search/search')}?q={quote(query)}"

            status = status_entry.get('status', 'missing')
            if date_str == today_str:
                status = 'locked'

            days_data.append({
                'date': date_str,
                'status': status,
                'count': status_entry.get('count', 0),
                'splunk_query': query,
                'splunk_link': splunk_link,
                'uploaded_at': status_entry.get('uploaded_at'),
                'is_locked': date_str == today_str
            })

        return {'days': days_data}

    @app.delete('/summaries/date/<date>')
    def clear_data(date):
        """Clear data for a specific date."""
        db = app.config['MONGO_DB']

        try:
            result = db['summaries'].delete_many({'date': date})
            # schedule a rebuild of the index as a background task
            threading.Thread(target=build_correlations, args=(db,)).start()
            # Reset status to "missing" by removing
            db['data_status'].delete_one({'date': date})
            return {'message': f'Deleted {result.deleted_count} records for {date}'}
        except Exception as e:
            logging.error(f'Error deleting data for {date}: {str(e)}')
            return {'message': f'Error deleting data: {str(e)}'}, 500

    @app.post('/summaries/upload')
    @app.input(UploadSchema, location='files')
    def upload_data(files_data):
        """Upload Splunk results via CSV file."""
        t0 = datetime.now()
        file = files_data['file']
        db = app.config['MONGO_DB']

        # We use stream to read large files without loading everything into memory
        stream = io.TextIOWrapper(file.stream, encoding='utf-8')
        reader = csv.DictReader(stream)

        upload_time = datetime.now()
        today_str = upload_time.strftime('%Y-%m-%d')
        # Data expires after LAST_N_DAYS + grace period
        expires_at = upload_time + timedelta(days=last_n_days + EXPIRE_GRACE_PERIOD)

        insert_batch = []
        correlated_ops = []
        correlated_rule_port_ops = []
        total_count = 0
        date_count = defaultdict(int)

        logging.info(f'Starting upload processing (passed={(datetime.now() - t0).seconds:.2f}s)')

        try:
            # We'll process the CSV in batches to allow parallel validation of batch items
            items_buffer = []
            
            def process_batch(current_items):
                nonlocal total_count, insert_batch, correlated_ops, correlated_rule_port_ops
                
                with ThreadPoolExecutor() as executor:
                    # Validate items in parallel
                    results = list(executor.map(
                        lambda x: validate_item(x[1], x[0], today_str, upload_time, expires_at),
                        current_items
                    ))
                
                for item, error in results:
                    if error:
                        raise ValueError(error)
                    if not item:
                        continue
                    
                    date_str = item['date']
                    date_count[date_str] += 1
                    
                    insert_batch.append(item)
                    for direction in ['src', 'dst']:
                        correlated_ops.append(UpdateOne(
                            {
                                'ip': item['src_ip'] if direction == 'src' else item['dest_ip'],
                                'rule_id': item['rule'],
                                'direction': direction,
                            },
                            {
                                '$inc': {f'activity.{item["date"]}': item['count']},
                                '$set': {'expires_at': item['expires_at']},
                            },
                            upsert=True,
                        ))

                    for port in item['ports']:
                        correlated_rule_port_ops.append(UpdateOne(
                            {
                                'rule_id': item['rule'],
                                'port': port,
                            },
                            {
                                '$inc': {f'activity.{item["date"]}': item['count']},
                                '$set': {'expires_at': item['expires_at']},
                            },
                            upsert=True,
                        ))

                    if len(insert_batch) >= UPLOAD_BATCH_SIZE:
                        db['summaries'].insert_many(insert_batch)
                        db['correlated_rule_ip'].bulk_write(correlated_ops)
                        db['correlated_rule_ports'].bulk_write(correlated_rule_port_ops)
                        total_count += len(insert_batch)
                        insert_batch = []
                        correlated_ops = []
                        correlated_rule_port_ops = []

            for i, row in enumerate(reader):
                items_buffer.append((i, row))
                if len(items_buffer) >= UPLOAD_BATCH_SIZE:
                    process_batch(items_buffer)
                    items_buffer = []
            
            if items_buffer:
                process_batch(items_buffer)

            if insert_batch:
                db['summaries'].insert_many(insert_batch)
                db['correlated_rule_ip'].bulk_write(correlated_ops)
                db['correlated_rule_ports'].bulk_write(correlated_rule_port_ops)
                total_count += len(insert_batch)

            # update cumulated data_status
            for date_str, count in date_count.items():
                db['data_status'].update_one(
                    {'date': date_str},
                    {'$inc': {'count': count}, '$set': {'uploaded_at': upload_time, 'status': 'present'}},
                    upsert=True
                )

        except ValueError as ve:
            return {'message': str(ve)}, 400
        except Exception as e:
            logging.error(f'Upload error: {str(e)}')
            return {'message': f'Upload failed: {str(e)}'}, 500

        if total_count == 0:
            return {'message': 'No data to upload (all records were for today or empty)'}, 200

        return {'message': f'Successfully uploaded {total_count} records'}, 201

    @app.get('/search/ip/<ip>')
    @app.output(IPSearchResultSchema)
    def search_ip(ip):
        """Search for activity by IP."""
        db = app.config['MONGO_DB']
        # Get range of dates for the last N days
        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        target_dates = [
            (today_start - timedelta(days=i)).strftime('%Y-%m-%d')
            for i in range(last_n_days + 1)
        ]
        
        # 1. present_dates for the lookback period
        missing_count, present_dates = get_missing_count(db, target_dates)

        # 2. Results from correlated_rule_ip
        src_hits_raw = list(db['correlated_rule_ip'].find({'ip': ip, 'direction': 'src'}))
        dst_hits_raw = list(db['correlated_rule_ip'].find({'ip': ip, 'direction': 'dst'}))
        
        timeline_results = defaultdict(int)
        src_hits = []
        for item in src_hits_raw:
            activity = item.get('activity', {})
            total_hits = sum(activity.values())
            last_activity = max(activity.keys()) if activity else None
            src_hits.append({
                'rule': item['rule_id'],
                'count': total_hits,
                'last_activity': last_activity
            })
            for date, count in activity.items():
                if date in target_dates:
                    timeline_results[date] += count

        dst_hits = []
        for item in dst_hits_raw:
            activity = item.get('activity', {})
            total_hits = sum(activity.values())
            last_activity = max(activity.keys()) if activity else None
            dst_hits.append({
                'rule': item['rule_id'],
                'count': total_hits,
                'last_activity': last_activity
            })
            for date, count in activity.items():
                if date in target_dates:
                    timeline_results[date] += count

        # Sort hits by count
        src_hits.sort(key=lambda x: x['count'], reverse=True)
        dst_hits.sort(key=lambda x: x['count'], reverse=True)

        # Ensure all days in range are present in timeline
        timeline = pad_timeline(timeline_results, present_dates, target_dates)
        
        warning = None
        if missing_count > 0:
            warning = f"missing data for {missing_count}/{len(target_dates)} days"

        return {
            'timeline': timeline,
            'src_hits': src_hits,
            'dst_hits': dst_hits,
            'warning': warning
        }

    @app.get('/search/rule/<rule>')
    @app.output(RuleSearchResultSchema)
    def search_rule(rule):
        """Search for activity by Firewall Rule."""
        db = app.config['MONGO_DB']
        # Get range of dates for the last N days
        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        target_dates = [
            (today_start - timedelta(days=i)).strftime('%Y-%m-%d')
            for i in range(last_n_days + 1)
        ]

        # 1. present_dates from data_status
        missing_count, present_dates = get_missing_count(db, target_dates)
        # 2. Results from correlated_rule_ip
        active_sources_raw = list(db['correlated_rule_ip'].find({'rule_id': rule, 'direction': 'src'}))
        active_destinations_raw = list(db['correlated_rule_ip'].find({'rule_id': rule, 'direction': 'dst'}))
        
        timeline_results = defaultdict(int)
        
        active_sources = []
        for item in active_sources_raw:
            activity = item.get('activity', {})
            total_hits = sum(activity.values())
            last_activity = max(activity.keys()) if activity else None
            active_sources.append({
                'ip': item['ip'],
                'count': total_hits,
                'last_activity': last_activity
            })
            for date, count in activity.items():
                if date in target_dates:
                    timeline_results[date] += count

        active_destinations = []
        for item in active_destinations_raw:
            activity = item.get('activity', {})
            total_hits = sum(activity.values())
            last_activity = max(activity.keys()) if activity else None
            active_destinations.append({
                'ip': item['ip'],
                'count': total_hits,
                'last_activity': last_activity
            })
            for date, count in activity.items():
                if date in target_dates:
                    timeline_results[date] += count

        # 3. Ports from correlated_rule_ports
        ports_raw = list(db['correlated_rule_ports'].find({'rule_id': rule}))
        ports = []
        for item in ports_raw:
            activity = item.get('activity', {})
            total_hits = sum(activity.values())
            last_activity = max(activity.keys()) if activity else None
            ports.append({
                'port': int(item['port']),
                'count': total_hits,
                'last_activity': last_activity
            })

        # Sort results
        active_sources.sort(key=lambda x: x['count'], reverse=True)
        active_sources = active_sources[:100]
        active_destinations.sort(key=lambda x: x['count'], reverse=True)
        active_destinations = active_destinations[:100]
        ports.sort(key=lambda x: x['count'], reverse=True)
        ports = ports[:100]

        # Ensure all days in range are present in timeline
        timeline = pad_timeline(timeline_results, present_dates, target_dates)

        warning = None
        if missing_count > 0:
            warning = f"missing data for {missing_count}/{len(target_dates)} days"

        return {
            'timeline': timeline,
            'ports': ports,
            'active_sources': active_sources,
            'active_destinations': active_destinations,
            'warning': warning
        }
