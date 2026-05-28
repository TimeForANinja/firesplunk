import os
from datetime import datetime, timedelta
from urllib.parse import quote
from apiflask import APIFlask, Schema, abort
from apiflask.fields import String, List, Dict, Integer, DateTime, Boolean
from dotenv import load_dotenv
from flask_cors import CORS
from pymongo import MongoClient

load_dotenv()

app = APIFlask(__name__, title='FireSplunk API', version='1.0', static_folder='static', static_url_path='')
CORS(app)

# MongoDB Configuration
MONGO_URI = os.getenv('MONGO_URI', 'mongodb://localhost:27017/')
SPLUNK_SERVER_URL = os.getenv('SPLUNK_SERVER_URL', 'https://splunk.example.com')
SPLUNK_QUERY_TEMPLATE = os.getenv('SPLUNK_QUERY_TEMPLATE', 'index=net-fw | stats count by src_ip dest_ip rule')
client = MongoClient(MONGO_URI)
db = client.get_database('firesplunk')
summaries_collection = db.summaries

# Schemas
class SummaryItemSchema(Schema):
    date = String(required=True)
    src_ip = String(required=True)
    dest_ip = String(required=True)
    rule = String(required=True)
    count = Integer(required=True)
    timestamp = String() # Optional more granular timestamp

class UploadSchema(Schema):
    data = List(Dict()) # We'll accept a list of dicts directly from Splunk export

class MissingDataSchema(Schema):
    date = String()
    status = String() # 'present' or 'missing'
    count = Integer()
    splunk_query = String()
    splunk_link = String()
    uploaded_at = DateTime()
    is_locked = Boolean()

class TimelinePointSchema(Schema):
    timestamp = String()
    count = Integer()

class IPSearchResultSchema(Schema):
    timeline = List(Dict())
    src_hits = List(Dict())
    dst_hits = List(Dict())

class RuleSearchResultSchema(Schema):
    timeline = List(Dict())
    active_sources = List(Dict())
    active_destinations = List(Dict())

class MissingDataResponseSchema(Schema):
    days = List(Dict())

# Endpoints
@app.get('/')
def index():
    return app.send_static_file('index.html')

@app.get('/health')
def health_check():
    return {'status': 'ok'}

@app.get('/summaries/missing')
@app.output(MissingDataResponseSchema)
def get_missing_data():
    """Get status of data for the last 30 days and Splunk queries for missing ones."""
    days_data = []
    now = datetime.now()
    today_str = now.strftime('%Y-%m-%d')
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    
    for i in range(0, 31):
        target_date = today_start - timedelta(days=i)
        date_str = target_date.strftime('%Y-%m-%d')
        
        # Check if we have data for this date
        first_entry = summaries_collection.find_one({'date': date_str})
        count = summaries_collection.count_documents({'date': date_str})
        
        # Splunk query
        earliest = target_date.strftime('%m/%d/%Y:00:00:00')
        latest = target_date.strftime('%m/%d/%Y:23:59:59')
        query = f'earliest="{earliest}" latest="{latest}" {SPLUNK_QUERY_TEMPLATE}'
        splunk_link = f"{SPLUNK_SERVER_URL}/en-US/app/search/search?q={quote(query)}"
        
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
    result = summaries_collection.delete_many({'date': date})
    return {'message': f'Deleted {result.deleted_count} records for {date}'}

@app.post('/summaries/upload')
@app.input(UploadSchema)
def upload_data(json_data):
    """Upload Splunk results (list of {src_ip, dest_ip, rule, count, [date]})"""
    data = json_data.get('data', [])
    if not data:
        return {'message': 'No data provided'}, 400
    
    upload_time = datetime.now()
    
    # Pre-process data to ensure date is present and count is integer
    for item in data:
        item['uploaded_at'] = upload_time
        # Cast count to int
        if 'count' in item:
            try:
                item['count'] = int(item['count'])
            except (ValueError, TypeError):
                item['count'] = 1
        else:
            item['count'] = 1

        if 'date' not in item and 'timestamp' in item:
            item['date'] = item['timestamp'][:10]
        elif 'date' not in item:
            # Fallback to today or some default if missing, but usually we expect it
            item['date'] = datetime.now().strftime('%Y-%m-%d')
            
    summaries_collection.insert_many(data)
    return {'message': f'Successfully uploaded {len(data)} records'}, 201

@app.get('/search/ip/<ip>')
@app.output(IPSearchResultSchema)
def search_ip(ip):
    """Search for activity by IP."""
    # Get list of days we have data for
    days_with_data = summaries_collection.distinct('date')
    
    # Timeline (by date)
    pipeline_timeline = [ 
        {'$match': {'$or': [{'src_ip': ip}, {'dest_ip': ip}]}},
        {'$group': {'_id': '$date', 'count': {'$sum': '$count'}}},
        {'$sort': {'_id': 1}}
    ]
    timeline_results = {item['_id']: item['count'] for item in summaries_collection.aggregate(pipeline_timeline)}
    
    # Ensure all days with data are present in timeline
    timeline = []
    for d in sorted(days_with_data):
        timeline.append({
            'timestamp': d,
            'count': timeline_results.get(d, 0)
        })

    # Source hits (grouped by rule)
    pipeline_src = [
        {'$match': {'src_ip': ip}},
        {'$group': {'_id': '$rule', 'count': {'$sum': '$count'}}},
        {'$sort': {'count': -1}}
    ]
    src_hits = list(summaries_collection.aggregate(pipeline_src))
    src_hits = [{'rule': item['_id'], 'count': item['count']} for item in src_hits]

    # Destination hits (grouped by rule)
    pipeline_dst = [
        {'$match': {'dest_ip': ip}},
        {'$group': {'_id': '$rule', 'count': {'$sum': '$count'}}},
        {'$sort': {'count': -1}}
    ]
    dst_hits = list(summaries_collection.aggregate(pipeline_dst))
    dst_hits = [{'rule': item['_id'], 'count': item['count']} for item in dst_hits]
    
    return {
        'timeline': timeline,
        'src_hits': src_hits,
        'dst_hits': dst_hits
    }

@app.get('/search/rule/<rule>')
@app.output(RuleSearchResultSchema)
def search_rule(rule):
    """Search for activity by Firewall Rule."""
    # Get list of days we have data for
    days_with_data = summaries_collection.distinct('date')

    # Timeline (by date)
    pipeline_timeline = [
        {'$match': {'rule': rule}},
        {'$group': {'_id': '$date', 'count': {'$sum': '$count'}}},
        {'$sort': {'_id': 1}}
    ]
    timeline_results = {item['_id']: item['count'] for item in summaries_collection.aggregate(pipeline_timeline)}
    
    # Ensure all days with data are present in timeline
    timeline = []
    for d in sorted(days_with_data):
        timeline.append({
            'timestamp': d,
            'count': timeline_results.get(d, 0)
        })

    # Active Sources
    pipeline_src = [
        {'$match': {'rule': rule}},
        {'$group': {'_id': '$src_ip', 'count': {'$sum': '$count'}}},
        {'$sort': {'count': -1}},
        {'$limit': 100}
    ]
    active_sources = list(summaries_collection.aggregate(pipeline_src))
    active_sources = [{'ip': item['_id'], 'count': item['count']} for item in active_sources]

    # Active Destinations
    pipeline_dst = [
        {'$match': {'rule': rule}},
        {'$group': {'_id': '$dest_ip', 'count': {'$sum': '$count'}}},
        {'$sort': {'count': -1}},
        {'$limit': 100}
    ]
    active_destinations = list(summaries_collection.aggregate(pipeline_dst))
    active_destinations = [{'ip': item['_id'], 'count': item['count']} for item in active_destinations]
    
    return {
        'timeline': timeline,
        'active_sources': active_sources,
        'active_destinations': active_destinations
    }

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
