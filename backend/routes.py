import logging
from collections import defaultdict
from datetime import datetime, timedelta
from urllib.parse import quote, urljoin
import csv
import io
from pymongo import UpdateOne

from db import build_correlations
from models import (
    MissingDataResponseSchema, UploadSchema, IPSearchResultSchema, RuleSearchResultSchema
)


UPLOAD_BATCH_SIZE = 1_000
EXPIRE_GRACE_PERIOD = 2


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
            # schedule a rebuild of the index
            build_correlations(db)
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

        required_fields = ['src_ip', 'dest_ip', 'rule', 'count', 'date']
        upload_time = datetime.now()
        today_str = upload_time.strftime('%Y-%m-%d')
        # Data expires after LAST_N_DAYS + grace period
        expires_at = upload_time + timedelta(days=last_n_days + EXPIRE_GRACE_PERIOD)

        insert_batch = []
        correlated_ops = []
        total_count = 0
        date_count = defaultdict(int)

        logging.info(f'Starting upload processing (passed={(datetime.now() - t0).seconds:.2f}s)')

        try:
            for i, item in enumerate(reader):
                # Check for missing fields
                missing = [
                    f
                    for f in required_fields
                    if f not in item
                    or item[f] is None
                    or str(item[f]).strip() == ''
                ]
                if missing:
                    return {'message': f'Record {i} is missing required fields: {", ".join(missing)}'}, 400

                date_str = item['date']
                if date_str == today_str:
                    # ignore any data for "today"
                    continue

                date_count[date_str] += 1

                item['uploaded_at'] = upload_time
                item['expires_at'] = expires_at

                # Validate count is integer
                try:
                    item['count'] = int(item['count'])
                except (ValueError, TypeError):
                    return {'message': f'Record {i} has invalid count: {item["count"]}'}, 400

                # validate/parse ports
                try:
                    ports_val = item.get('ports', '')
                    item['ports'] = [] if not ports_val else [int(p) for p in ports_val.split(':')]
                except (ValueError, TypeError):
                    return {'message': f'Record {i} has invalid ports: {item["ports"]}'}, 400

                # item has been validated, queue it for insert
                insert_batch.append(item)

                # queue an update on the correlated_data collection
                for direction in ['src', 'dst']:
                    correlated_ops.append(UpdateOne(
                        {
                            'ip': item['src_ip'],
                            'rule_id': item['rule'],
                            'direction': direction,
                        },
                        {
                            '$inc': {'hit_count': item['count']},
                            '$max': {'last_seen': item['date']},
                            '$min': {'first_seen': item['date']},
                            '$set': {'expires_at': item['expires_at']},
                        },
                        upsert=True,
                    ))

                if len(insert_batch) >= UPLOAD_BATCH_SIZE:
                    db['summaries'].insert_many(insert_batch)
                    db['correlated_data'].bulk_write(correlated_ops)
                    total_count += len(insert_batch)
                    insert_batch = []
                    correlated_ops = []

            if insert_batch:
                db['summaries'].insert_many(insert_batch)
                db['correlated_data'].bulk_write(correlated_ops)
                total_count += len(insert_batch)

            # update cumulated data_status
            for date_str, count in date_count.items():
                db['data_status'].update_one(
                    {'date': date_str},
                    {'$inc': {'count': count}, '$set': {'uploaded_at': upload_time, 'status': 'present'}},
                    upsert=True
                )

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
        
        # 1. Timeline (still need to use summaries or we could have aggregated timeline in correlated)
        # Actually, correlated_data doesn't have daily counts, it has hit_count per IP/rule/dir.
        # So for timeline we still need summaries OR a third collection.
        # The request said: "only include data we care about... hit_count: 123, last_seen: ISODate"
        # This structure is NOT good for timeline if we want daily granularity.
        # But maybe the user meant we should use correlated_data for the hits tables.
        
        # Let's keep timeline on summaries for now, as it's date-specific.
        # BUT the hits (src_hits, dst_hits) can come from correlated_data!
        
        # For timeline, we can still use the efficient summaries index on date.
        pipeline_timeline = [
            {'$match': {'$or': [{'src_ip': ip}, {'dest_ip': ip}], 'date': {'$in': target_dates}}},
            {'$group': {'_id': '$date', 'count': {'$sum': '$count'}}},
            {'$sort': {'_id': 1}}
        ]
        timeline_raw = list(db['summaries'].aggregate(pipeline_timeline))
        timeline_results = {item['_id']: item['count'] for item in timeline_raw}

        # 2. Hits from correlated_data
        src_hits_raw = db['correlated_data'].find({'ip': ip, 'direction': 'src'}).sort('hit_count', -1)
        src_hits = [
            {'rule': item['rule_id'], 'count': item['hit_count'], 'last_activity': item['last_seen']}
            for item in src_hits_raw
        ]
        
        dst_hits_raw = db['correlated_data'].find({'ip': ip, 'direction': 'dst'}).sort('hit_count', -1)
        dst_hits = [
            {'rule': item['rule_id'], 'count': item['hit_count'], 'last_activity': item['last_seen']}
            for item in dst_hits_raw
        ]

        # 3. present_dates for the lookback period
        # Use data_status which is already fast!
        status_results = list(db['data_status'].find({'date': {'$in': target_dates}, 'status': 'present'}))
        present_dates = {item['date'] for item in status_results}
        missing_count = len(set(target_dates) - present_dates)

        # Ensure all days in range are present in timeline
        timeline = []
        for d in sorted(target_dates):
            count = timeline_results.get(d, 0)
            timeline.append({
                'timestamp': d,
                'count': count,
                'has_data': d in present_dates
            })
        
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

        # 1. Timeline from summaries
        pipeline_timeline = [
            {'$match': {'rule': rule, 'date': {'$in': target_dates}}},
            {'$group': {'_id': '$date', 'count': {'$sum': '$count'}}},
            {'$sort': {'_id': 1}}
        ]
        timeline_raw = list(db['summaries'].aggregate(pipeline_timeline))
        timeline_results = {item['_id']: item['count'] for item in timeline_raw}

        # 2. Active sources and destinations from correlated_data
        active_sources_raw = db['correlated_data'].find({'rule_id': rule, 'direction': 'src'}).sort('hit_count', -1).limit(100)
        active_sources = [
            {'ip': item['ip'], 'count': item['hit_count'], 'last_activity': item['last_seen']}
            for item in active_sources_raw
        ]
        
        active_destinations_raw = db['correlated_data'].find({'rule_id': rule, 'direction': 'dst'}).sort('hit_count', -1).limit(100)
        active_destinations = [
            {'ip': item['ip'], 'count': item['hit_count'], 'last_activity': item['last_seen']}
            for item in active_destinations_raw
        ]

        # 3. Ports - still need to use summaries because ports are in a list there 
        # and we didn't add ports to correlated_data schema requested by user.
        # User only said: "ip", "rule_id", "direction", "hit_count", "last_seen"
        pipeline_ports = [
            {'$match': {'rule': rule}},
            {'$unwind': '$ports'},
            {'$group': {
                '_id': '$ports',
                'count': {'$sum': '$count'},
                'last_activity': {'$max': '$date'}
            }},
            {'$sort': {'count': -1}},
            {'$limit': 100}
        ]
        ports_raw = list(db['summaries'].aggregate(pipeline_ports))
        ports = [
            {'port': int(item['_id']), 'count': item['count'], 'last_activity': item['last_activity']}
            for item in ports_raw
        ]

        # 4. present_dates from data_status
        status_results = list(db['data_status'].find({'date': {'$in': target_dates}, 'status': 'present'}))
        present_dates = {item['date'] for item in status_results}
        missing_count = len(set(target_dates) - present_dates)

        # Ensure all days in range are present in timeline
        timeline = []
        for d in sorted(target_dates):
            count = timeline_results.get(d, 0)
            timeline.append({
                'timestamp': d,
                'count': count,
                'has_data': d in present_dates
            })

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
