import os

def get_mongo_uri() -> str:
    return os.environ.get('APP_MONGO_URI', 'mongodb://localhost:27017/')

def get_last_n_days() -> int:
    return int(os.environ.get('APP_LAST_N_DAYS', '30'))

def get_splunk_server_url() -> str:
    return os.environ.get('APP_SPLUNK_SERVER_URL', 'https://splunk.example.com')

def get_splunk_query_template() -> str:
    return os.environ.get('APP_SPLUNK_QUERY_TEMPLATE', 'index=net-fw | stats count, values(dest_port) as ports by src_ip dest_ip rule')
