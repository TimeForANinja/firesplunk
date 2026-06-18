from apiflask import APIFlask
from flask_cors import CORS

from shared.env import get_mongo_uri
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
client = MongoClient(get_mongo_uri())
db = client.get_database('firesplunk')
app.config['MONGO_DB'] = db
app.config['TASK_MANAGER'] = TaskManager(db)

register_routes(app)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
