import os

def get_mongo_uri() -> str:
    return os.environ.get('APP_MONGO_URI', 'mongodb://localhost:27017/')

def get_last_n_days() -> int:
    return int(os.environ.get('APP_LAST_N_DAYS', '30'))

def get_splunk_server_url() -> str:
    return os.environ.get('APP_SPLUNK_SERVER_URL', 'https://splunk.example.com')

def get_splunk_query_template() -> str:
    return os.environ.get('APP_SPLUNK_QUERY_TEMPLATE', 'index=net-fw | stats count by src_ip dest_ip dest_port rule')

def get_splunk_username() -> str:
    return os.environ.get('APP_SPLUNK_USERNAME', '')

def get_splunk_password() -> str:
    return os.environ.get('APP_SPLUNK_PASSWORD', '')

def get_splunk_port() -> int:
    return int(os.environ.get('APP_SPLUNK_PORT', '8089'))

def get_splunk_verify_ssl() -> bool:
    return os.environ.get('APP_SPLUNK_VERIFY_SSL', 'false').lower() in ('1', 'true', 'yes')

def get_splunk_poll_interval() -> int:
    return int(os.environ.get('APP_SPLUNK_POLL_INTERVAL', '5'))
