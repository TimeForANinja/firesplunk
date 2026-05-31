from datetime import datetime, timedelta
from urllib.parse import quote, urljoin
from models import (
    MissingDataResponseSchema, UploadSchema, IPSearchResultSchema, RuleSearchResultSchema
)

def register_routes(app):
    # read required vars from config
    last_n_days = int(app.config.get('LAST_N_DAYS', 30))
    splunk_server_url = app.config.get('SPLUNK_SERVER_URL', 'https://splunk.example.com')
    splunk_query_template = app.config.get('SPLUNK_QUERY_TEMPLATE', 'index=net-fw | stats count, values(dest_port) as ports by src_ip dest_ip rule')
    print("register_routes({}, {}, {})".format(last_n_days, splunk_server_url, splunk_query_template))

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
        
        for i in range(0, last_n_days + 1):
            target_date = today_start - timedelta(days=i)
            date_str = target_date.strftime('%Y-%m-%d')
            
            # Check if we have data for this date
            first_entry = app.config['MONGO_DB']['summaries'].find_one({'date': date_str})
            count = app.config['MONGO_DB']['summaries'].count_documents({'date': date_str})
            
            # Splunk query
            earliest = target_date.strftime('%m/%d/%Y:00:00:00')
            latest = target_date.strftime('%m/%d/%Y:23:59:59')
            query = f'earliest="{earliest}" latest="{latest}" {splunk_query_template}'
            splunk_link = f"{urljoin(splunk_server_url, '/en-US/app/search/search')}?q={quote(query)}"
            
            days_data.append({
                'date': date_str,
                'status': 'present' if count > 0 else 'missing',
                'count': count,
                'splunk_query': query,
                'splunk_link': splunk_link,
                'uploaded_at': first_entry.get('uploaded_at') if first_entry else None,
                'is_locked': date_str == today_str
            })
                
        return {'days': days_data}

    @app.delete('/summaries/date/<date>')
    def clear_data(date):
        """Clear data for a specific date."""
        result = app.config['MONGO_DB']['summaries'].delete_many({'date': date})
        return {'message': f'Deleted {result.deleted_count} records for {date}'}

    @app.post('/summaries/upload')
    @app.input(UploadSchema)
    def upload_data(json_data):
        """Upload Splunk results (list of {src_ip, dest_ip, rule, count, date, ports})"""
        data = json_data.get('data', [])
        if not data:
            return {'message': 'No data provided'}, 400
        
        required_fields = ['src_ip', 'dest_ip', 'rule', 'count', 'date', 'ports']
        upload_time = datetime.now()
        # Data expires after LAST_N_DAYS + 2
        expires_at = upload_time + timedelta(days=last_n_days + 2)
        
        # Pre-process data and validate
        for i, item in enumerate(data):
            # Check for missing fields
            missing = [
                f
                for f in required_fields
                if f not in item or item[f] is None or str(item[f]).strip() == ''
            ]
            if missing:
                return {'message': f'Record {i} is missing required fields: {", ".join(missing)}'}, 400
            
            item['uploaded_at'] = upload_time
            item['expires_at'] = expires_at

            # Validate count is integer
            try:
                item['count'] = int(item['count'])
            except (ValueError, TypeError):
                return {'message': f'Record {i} has invalid count: {item["count"]}'}, 400

            # Handle ports (a list of ports, separated by ":")
            try:
                item['ports'] = [int(p) for p in item['ports'].split(':')]
            except (ValueError, TypeError):
                return {'message': f'Record {i} has invalid ports: {item["ports"]}'}, 400
                
        app.config['MONGO_DB']['summaries'].insert_many(data)
        # Ensure TTL index exists
        app.config['MONGO_DB']['summaries'].create_index("expires_at", expireAfterSeconds=0)
        
        return {'message': f'Successfully uploaded {len(data)} records'}, 201

    @app.get('/search/ip/<ip>')
    @app.output(IPSearchResultSchema)
    def search_ip(ip):
        """Search for activity by IP."""
        # Get range of dates for the last N days
        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        target_dates = [
            (today_start - timedelta(days=i)).strftime('%Y-%m-%d')
            for i in range(last_n_days + 1)
        ]
        
        # Check which days have NO data at all in the database
        pipeline_missing = [
            {'$match': {'date': {'$in': target_dates}}},
            {'$group': {'_id': '$date'}}
        ]
        present_dates = {
            item['_id']
            for item in app.config['MONGO_DB']['summaries'].aggregate(pipeline_missing)
        }
        missing_count = len(set(target_dates) - present_dates)

        # Timeline (by date) for this IP
        pipeline_timeline = [ 
            {'$match': {'$or': [{'src_ip': ip}, {'dest_ip': ip}]}},
            {'$group': {'_id': '$date', 'count': {'$sum': '$count'}}},
            {'$sort': {'_id': 1}}
        ]
        timeline_results = {
            item['_id']: item['count']
            for item
            in app.config['MONGO_DB']['summaries'].aggregate(pipeline_timeline)
        }
        
        # Ensure all days in range are present in timeline
        timeline = []
        for d in sorted(target_dates):
            count = timeline_results.get(d, 0)
            timeline.append({
                'timestamp': d,
                'count': count
            })

        # Source hits (grouped by rule)
        pipeline_src = [
            {'$match': {'src_ip': ip}},
            {'$group': {
                '_id': '$rule', 
                'count': {'$sum': '$count'},
                'last_activity': {'$max': '$date'}
            }},
            {'$sort': {'count': -1}}
        ]
        src_hits = list(app.config['MONGO_DB']['summaries'].aggregate(pipeline_src))
        src_hits = [
            {'rule': item['_id'], 'count': item['count'], 'last_activity': item['last_activity']}
            for item in src_hits
        ]

        # Destination hits (grouped by rule)
        pipeline_dst = [
            {'$match': {'dest_ip': ip}},
            {'$group': {
                '_id': '$rule', 
                'count': {'$sum': '$count'},
                'last_activity': {'$max': '$date'}
            }},
            {'$sort': {'count': -1}}
        ]
        dst_hits = list(app.config['MONGO_DB']['summaries'].aggregate(pipeline_dst))
        dst_hits = [
            {'rule': item['_id'], 'count': item['count'], 'last_activity': item['last_activity']}
            for item in dst_hits
        ]
        
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
        # Get range of dates for the last N days
        now = datetime.now()
        today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        target_dates = [
            (today_start - timedelta(days=i)).strftime('%Y-%m-%d')
            for i in range(last_n_days + 1)
        ]

        # Check which days have NO data at all in the database
        pipeline_missing = [
            {'$match': {'date': {'$in': target_dates}}},
            {'$group': {'_id': '$date'}}
        ]
        present_dates = {
            item['_id']
            for item in app.config['MONGO_DB']['summaries'].aggregate(pipeline_missing)
        }
        missing_count = len(set(target_dates) - present_dates)

        # Timeline (by date) for this Rule
        pipeline_timeline = [
            {'$match': {'rule': rule}},
            {'$group': {'_id': '$date', 'count': {'$sum': '$count'}}},
            {'$sort': {'_id': 1}}
        ]
        timeline_results = {
            item['_id']: item['count']
            for item
            in app.config['MONGO_DB']['summaries'].aggregate(pipeline_timeline)
        }
        
        # Ensure all days in range are present in timeline
        timeline = []
        for d in sorted(target_dates):
            count = timeline_results.get(d, 0)
            timeline.append({
                'timestamp': d,
                'count': count
            })

        # Active Sources
        pipeline_src = [
            {'$match': {'rule': rule}},
            {'$group': {
                '_id': '$src_ip', 
                'count': {'$sum': '$count'},
                'last_activity': {'$max': '$date'}
            }},
            {'$sort': {'count': -1}},
            {'$limit': 100}
        ]
        active_sources = list(app.config['MONGO_DB']['summaries'].aggregate(pipeline_src))
        active_sources = [
            {'ip': item['_id'], 'count': item['count'], 'last_activity': item['last_activity']}
            for item in active_sources
        ]

        # Active Destinations
        pipeline_dst = [
            {'$match': {'rule': rule}},
            {'$group': {
                '_id': '$dest_ip', 
                'count': {'$sum': '$count'},
                'last_activity': {'$max': '$date'}
            }},
            {'$sort': {'count': -1}},
            {'$limit': 100}
        ]
        active_destinations = list(app.config['MONGO_DB']['summaries'].aggregate(pipeline_dst))
        active_destinations = [
            {'ip': item['_id'], 'count': item['count'], 'last_activity': item['last_activity']}
            for item in active_destinations
        ]
        
        warning = None
        if missing_count > 0:
            warning = f"missing data for {missing_count}/{len(target_dates)} days"

        return {
            'timeline': timeline,
            'active_sources': active_sources,
            'active_destinations': active_destinations,
            'warning': warning
        }
