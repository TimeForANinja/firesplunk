from datetime import datetime
from urllib.parse import quote, urljoin
from collections import defaultdict

from models import (
    MissingDataResponseSchema, UploadSchema, IPSearchResultSchema, RuleSearchResultSchema,
    IndexStateSchema, TaskListSchema
)
from utils import get_target_dates, get_missing_count, pad_timeline

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
            for item in app.config['MONGO_DB']['data_status'].find({'date': {'$in': target_dates}})
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

    @app.get('/index/state')
    @app.output(IndexStateSchema)
    def get_index_state():
        """Get the current state of the index."""
        return app.config['TASK_MANAGER'].get_index_state()

    @app.post('/index/rebuild')
    def request_rebuild():
        """Manually request an index rebuild."""
        app.config['TASK_MANAGER'].add_task('BUILD_INDEX')
        return {'message': 'Rebuild requested'}

    @app.get('/tasks')
    @app.output(TaskListSchema)
    def get_tasks():
        """Get list of active and recent tasks."""
        tasks = app.config['TASK_MANAGER'].get_tasks(limit_done=3)
        return {'tasks': tasks}

    @app.delete('/summaries/date/<date>')
    def clear_data(date):
        """Clear data for a specific date."""
        app.config['TASK_MANAGER'].add_task('DELETE_DATE', {'date': date})
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
        task_manager.add_task('UPLOAD_DATA', {
            'file_id': file_id,
            'filename': file.filename
        })
        
        return {'message': f'Upload of {file.filename} scheduled'}, 202

    @app.get('/search/ip/<ip>')
    @app.output(IPSearchResultSchema)
    def search_ip(ip):
        """Search for activity by IP."""
        db = app.config['MONGO_DB']
        # Get range of dates for the last N days
        target_dates = get_target_dates(last_n_days)
        
        # fetch date-range for the lookback period
        missing_count, present_dates = get_missing_count(db, target_dates)

        # fetch results from correlated_rule_ip
        src_hits_raw = list(db['correlated_rule_ip'].find({'ip': ip, 'direction': 'src'}))
        dst_hits_raw = list(db['correlated_rule_ip'].find({'ip': ip, 'direction': 'dst'}))

        # sum up activity counters per src/dst/day
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

        # (default) sort hits by count
        src_hits.sort(key=lambda x: x['count'], reverse=True)
        dst_hits.sort(key=lambda x: x['count'], reverse=True)

        # Ensure all days in range are present in timeline, defaulting to 0 otherwise
        timeline = pad_timeline(timeline_results, present_dates, target_dates)

        warning = None
        if missing_count > 0:
            warning = f"missing data for {missing_count}/{len(target_dates)} days"

        index_state = app.config['TASK_MANAGER'].get_index_state()
        if index_state['state'] != 'up-to-date':
            index_warning = "results may be inaccurate, index out-of-date"
            warning = f"{warning}; {index_warning}" if warning else index_warning

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

        # fetch date-range for the lookback period
        missing_count, present_dates = get_missing_count(db, target_dates)
        # fetch results from correlated_rule_ip
        active_sources_raw = list(db['correlated_rule_ip'].find({'rule_id': rule, 'direction': 'src'}))
        active_destinations_raw = list(db['correlated_rule_ip'].find({'rule_id': rule, 'direction': 'dst'}))

        # sum up activity counters per src/dst/day
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

        # fetch results from correlated_rule_ports, and sum activity counters per port/day
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
        active_destinations.sort(key=lambda x: x['count'], reverse=True)
        ports.sort(key=lambda x: x['count'], reverse=True)

        # Ensure all days in range are present in timeline
        timeline = pad_timeline(timeline_results, present_dates, target_dates)

        warning = None
        if missing_count > 0:
            warning = f"missing data for {missing_count}/{len(target_dates)} days"
        
        index_state = app.config['TASK_MANAGER'].get_index_state()
        if index_state['state'] != 'up-to-date':
            index_warning = "results may be inaccurate, index out-of-date"
            warning = f"{warning}; {index_warning}" if warning else index_warning

        return {
            'timeline': timeline,
            'ports': ports,
            'active_sources': active_sources,
            'active_destinations': active_destinations,
            'warning': warning
        }
