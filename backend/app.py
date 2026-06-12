from apiflask import APIFlask
from flask_cors import CORS

from db import init_db
from routes import register_routes
from task_manager import TaskManager
from pymongo import MongoClient


app = APIFlask(
    __name__,
    title='FireSplunk API',
    version='1.0.0',
    docs_path='/docs',
    static_folder='static',
    static_url_path=''
)
CORS(app)

# Swagger UI configuration for offline use
app.config['SWAGGER_UI_BUNDLE_JS'] = '/vendor/js/swagger-ui-bundle.js'
app.config['SWAGGER_UI_STANDALONE_PRESET_JS'] = '/vendor/js/swagger-ui-standalone-preset.js'
app.config['SWAGGER_UI_CSS'] = '/vendor/css/swagger-ui.css'


# load env variables into app.config
# overwrite the default loads, to keep properties as strings instead of doing a JSON parse
app.config.from_prefixed_env(prefix='APP', loads=lambda x: x)


# MongoDB Configuration
client = MongoClient(app.config.get('MONGO_URI', 'mongodb://localhost:27017/'))
db = client.get_database('firesplunk')

# Initialize Task Manager
task_manager = TaskManager(db)


app.config['TASK_MANAGER'] = task_manager
app.config['MONGO_DB'] = init_db(db, index_manager=task_manager)

register_routes(app)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
