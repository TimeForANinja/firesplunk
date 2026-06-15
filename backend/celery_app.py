import os
from celery import Celery

def make_celery():
    broker_url = os.environ.get('APP_CELERY_BROKER_URL', 'redis://localhost:6379/0')
    result_backend = os.environ.get('APP_CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
    
    celery = Celery(
        'firesplunk',
        broker=broker_url,
        backend=result_backend,
    )
    
    return celery

celery_app = make_celery()

# Import tasks to register them
import task_manager
