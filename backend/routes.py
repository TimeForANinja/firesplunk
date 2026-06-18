import logging
import time
from datetime import datetime
from urllib.parse import quote, urljoin
from collections import defaultdict

from models import (
    MissingDataResponseSchema, UploadSchema, IPSearchResultSchema, RuleSearchResultSchema,
    IndexStateSchema, TaskListSchema
)
from shared.env import get_last_n_days, get_splunk_server_url, get_splunk_query_template
from shared.date import get_target_dates
from utils import get_missing_count, pad_timeline, sum_activity_counters, check_for_warnings

UPLOAD_BATCH_SIZE = 1_000
EXPIRE_GRACE_PERIOD = 2


def register_routes(app):
    # read required vars
    last_n_days = get_last_n_days()
    splunk_server_url = get_splunk_server_url()
    splunk_query_template = get_splunk_query_template()

    @app.get('/')
    def index():
        return app.send_static_file('index.html')

    @app.get('/health')
    def health_check():
        return {'status': 'ok'}

    @app.get('/summaries/status')
    @app.output(MissingDataResponseSchema)
    def get_status_data():
        """Get status of data for the last N days and Splunk queries for missing ones."""
        days_data = []
        now = datetime.now()
        today_str = now.strftime('%Y-%m-%d')

        # Get range of dates for the last N days
        target_dates = get_target_dates(last_n_days)

        # Fetch status from data_status collection
        status_results = {
            item['date']: item
            for item
            in app.config['MONGO_DB']['data_status'].find({'date': {'$in': target_dates}})
        }

        for date_str in sorted(target_dates, reverse=True):
            target_date = datetime.strptime(str(date_str), '%Y-%m-%d')
            status_entry = status_results.get(date_str, {})

            # build splunk query
            earliest = target_date.strftime('%m/%d/%Y:00:00:00')
            latest = target_date.strftime('%m/%d/%Y:23:59:59')
            query = f'earliest="{earliest}" latest="{latest}" {splunk_query_template}'
            splunk_link = f"{urljoin(splunk_server_url, '/en-US/app/search/search')}?q={quote(query)}"

            if date_str == today_str:
                status = 'locked'
            elif date_str in status_results:
                status = 'present'
            else:
                status = 'missing'

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

    @app.post('/index/rebuild')
    def request_rebuild():
        """Manually request an index rebuild."""
        app.config['TASK_MANAGER'].add_build_index_task()
        return {'message': 'Rebuild requested'}

    @app.get('/tasks')
    @app.output(TaskListSchema)
    def get_tasks():
        """Get list of active and recent tasks."""
        tasks = app.config['TASK_MANAGER'].get_tasks(3)
        return {'tasks': tasks}

    @app.delete('/summaries/date/<date>')
    def clear_data(date):
        """Clear data for a specific date."""
        app.config['TASK_MANAGER'].add_delete_date_task(date)
        return {'message': f'Delete task for {date} scheduled'}

    @app.post('/summaries/upload')
    @app.input(UploadSchema, location='files')
    def upload_data(files_data):
        """Upload Splunk results via CSV file."""
        file = files_data['file']
        task_manager = app.config['TASK_MANAGER']
        
        # Save file to GridFS
        file_id = task_manager.fs.put(file.stream, filename=file.filename)
        
        # Create task
        task_manager.add_upload_data_task(
            str(file_id),
            file.filename
        )
        
        return {'message': f'Upload of {file.filename} scheduled'}, 202

    @app.get('/search/ip/<ip>')
    @app.output(IPSearchResultSchema)
    def search_ip(ip):
        """Search for activity by IP."""
        db = app.config['MONGO_DB']
        # Get range of dates for the last N days
        target_dates = get_target_dates(last_n_days)

        # fetch results from correlated_rule_ip
        hits_raw = list(db['correlated_rule_ip'].find({'ip': ip}))
        # we want both src and dst activity, so we need to separate them
        src_hits, timeline_src = sum_activity_counters(hits_raw, 'rule', 'activity-src')
        dst_hits, timeline_dst = sum_activity_counters(hits_raw, 'rule', 'activity-dst')

        timeline_results = defaultdict(int)
        all_dates = set(timeline_src.keys()) | set(timeline_dst.keys())
        for d in all_dates:
            timeline_results[d] = timeline_src.get(d, 0) + timeline_dst.get(d, 0)

        # (default) sort hits by count
        src_hits.sort(key=lambda x: x['count'], reverse=True)
        dst_hits.sort(key=lambda x: x['count'], reverse=True)

        # Ensure all days in range are present in timeline, defaulting to 0 otherwise
        _, present_dates = get_missing_count(db, target_dates)
        timeline = pad_timeline(dict(timeline_results), present_dates, target_dates)

        warning = check_for_warnings(db, target_dates, app.config['TASK_MANAGER'])

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
        target_dates = get_target_dates(last_n_days)

        # fetch results from correlated_rule_ip
        active_sources_raw = list(db['correlated_rule_ip'].find({'rule': rule}))
        # we want both src and dst activity, so we need to separate them
        active_sources, timeline_src = sum_activity_counters(active_sources_raw, 'ip', 'activity-src')
        active_destinations, timeline_dst = sum_activity_counters(active_sources_raw, 'ip', 'activity-dst')

        timeline_results = defaultdict(int)
        all_dates = set(timeline_src.keys()) | set(timeline_dst.keys())
        for d in all_dates:
            timeline_results[d] = timeline_src.get(d, 0) + timeline_dst.get(d, 0)

        # fetch results from correlated_rule_ports, and sum activity counters per port/day
        ports_raw = list(db['correlated_rule_ports'].find({'rule': rule}))
        ports, _ = sum_activity_counters(ports_raw, 'port')

        # Sort results
        active_sources.sort(key=lambda x: x['count'], reverse=True)
        active_destinations.sort(key=lambda x: x['count'], reverse=True)
        ports.sort(key=lambda x: x['count'], reverse=True)

        # Ensure all days in range are present in timeline
        _, present_dates = get_missing_count(db, target_dates)
        timeline = pad_timeline(dict(timeline_results), present_dates, target_dates)

        warning = check_for_warnings(db, target_dates, app.config['TASK_MANAGER'])

        return {
            'timeline': timeline,
            'ports': ports,
            'active_sources': active_sources,
            'active_destinations': active_destinations,
            'warning': warning
        }
