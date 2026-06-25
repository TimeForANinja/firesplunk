import time
import logging
import signal
from datetime import datetime, timedelta
from pymongo import MongoClient

from shared.env import get_mongo_uri
from db import init_db
from shared.tasks import TaskState
from tasks.build_index import BuildIndexTask
from tasks.delete_date import DeleteDateTask
from tasks.upload_data import UploadDataTask

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')


class Worker:
    def __init__(self):
        mongo_uri = get_mongo_uri()
        self.client = MongoClient(mongo_uri)
        self.db = self.client.get_database('firesplunk')
        self.running = True

        # Initialize DB (and handle initial index build if needed)
        init_db(self.db)

    def stop(self, signum, frame):
        logging.info("Stopping worker...")
        self.running = False

    def run(self):
        logging.info("Worker started, polling for tasks...")
        while self.running:
            try:
                self.check_stale_tasks()
                self.process_next_task()
            except Exception as e:
                logging.error(f"Error in worker loop: {e}", exc_info=True)

            time.sleep(5)

    def check_stale_tasks(self):
        # Mark tasks that haven't had a heartbeat in 5 minutes as STALE
        # We only care about work-in-progress tasks
        stale_threshold = datetime.now() - timedelta(minutes=5)
        
        # Also handle tasks that were just started but never got a heartbeat
        # (they only have last_state_change)
        
        result = self.db.tasks.update_many(
            {
                'state': TaskState.WORK_IN_PROGRESS.value,
                '$or': [
                    {'last_heartbeat': {'$lt': stale_threshold}},
                    {
                        'last_heartbeat': {'$exists': False},
                        'last_state_change': {'$lt': stale_threshold}
                    }
                ]
            },
            {
                '$set': {
                    'state': TaskState.STALE.value,
                    'additional_info': 'Task marked as stale (no heartbeat for 5m)'
                }
            }
        )
        if result.modified_count > 0:
            logging.info(f"Marked {result.modified_count} tasks as stale")

    def process_next_task(self):
        # Find a scheduled task and mark it as work-in-progress atomically
        task_doc = self.db.tasks.find_one_and_update(
            {'state': 'scheduled'},
            {'$set': {
                'state': TaskState.WORK_IN_PROGRESS.value,
                'last_state_change': datetime.now(),
                'progress': 0,
                'additional_info': 'Starting...'
            }},
            sort=[('created_at', 1)]
        )

        if not task_doc:
            return

        task_id = task_doc['_id']
        task_type = task_doc['type']
        data = task_doc.get('data', {})

        logging.info(f"Executing task {task_id} of type {task_type}")

        try:
            if task_type == 'BUILD_INDEX':
                task_obj = BuildIndexTask(self.db, task_id, data)
            elif task_type == 'DELETE_DATE':
                task_obj = DeleteDateTask(self.db, task_id, data)
            elif task_type == 'UPLOAD_DATA':
                task_obj = UploadDataTask(self.db, task_id, data)
            else:
                raise ValueError(f"Unknown task type: {task_type}")

            if task_doc.get('is_retry'):
                logging.info(f"Retrying task {task_id}")
                state, info = task_obj.retry()
            else:
                state, info = task_obj.run()

            self.db.tasks.update_one(
                {'_id': task_id},
                {'$set': {
                    'state': state.value,
                    'additional_info': info,
                    'last_state_change': datetime.now()
                }}
            )
            logging.info(f"Task {task_id} completed successfully")

        except Exception as e:
            logging.exception(f"Error executing task {task_id}")
            self.db.tasks.update_one(
                {'_id': task_id},
                {'$set': {
                    'state': 'failed',
                    'additional_info': f"Error: {str(e)}",
                    'last_state_change': datetime.now()
                }}
            )

if __name__ == '__main__':
    worker = Worker()
    signal.signal(signal.SIGINT, worker.stop)
    signal.signal(signal.SIGTERM, worker.stop)
    worker.run()
