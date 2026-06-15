import logging
import uuid
import os
import time
from datetime import datetime
import gridfs
from pymongo import MongoClient
from celery_app import celery_app
from db import build_correlations

DEBOUNCE_SECONDS = 300
INDEX_REBUILD_TASK_ID = "index-rebuild-scheduled"

def get_db():
    mongo_uri = os.environ.get('APP_MONGO_URI', 'mongodb://localhost:27017/')
    client = MongoClient(mongo_uri)
    return client.get_database('firesplunk')

@celery_app.task(bind=True)
def run_task_celery(self, task_id, task_type, data=None):
    db = get_db()
    fs = gridfs.GridFS(db)
    
    start_time = time.time()

    def update_task_in_db(state=None, progress=None, info=None):
        update_data = {'last_state_change': datetime.now()}
        if state:
            update_data['state'] = state
        if progress is not None:
            update_data['progress'] = progress
        if info is not None:
            update_data['additional_info'] = info
        db.tasks.update_one({'_id': task_id}, {'$set': update_data})

    def progress_callback(p, extra=""):
        info = f"{p}%"
        elapsed = time.time() - start_time
        if task_type == 'UPLOAD_DATA':
            if elapsed > 60:
                info += f" - {int(elapsed/60)}m elapsed"
            else:
                info += f" - {int(elapsed)}s elapsed"
        elif p > 0:
            total_est = elapsed / (p / 100.0)
            remaining = total_est - elapsed
            if remaining > 60:
                info += f" - ~{int(remaining/60)}m left"
            else:
                info += f" - ~{int(remaining)}s left"
        if extra:
            info += f" ({extra})"
        update_task_in_db(progress=p, info=info)

    update_task_in_db(state='work-in-progress', info='Starting...')

    try:
        if task_type == 'BUILD_INDEX':
            build_correlations(db, progress_callback=progress_callback)
        elif task_type == 'DELETE_DATE':
            date = data['date']
            progress_callback(0, f"Deleting records for {date}")
            result = db['summaries'].delete_many({'date': date})
            progress_callback(50, f"Deleted {result.deleted_count} records. Updating status...")
            db['data_status'].delete_one({'date': date})
            progress_callback(90, "Requesting index rebuild")
            
            # Use TaskManager to add the task (to handle debounce)
            tm = TaskManager(db)
            tm.add_task('BUILD_INDEX')
            
            progress_callback(100, "Done")
        elif task_type == 'UPLOAD_DATA':
            from utils import process_upload_stream
            file_id = data['file_id']
            filename = data['filename']
            progress_callback(0, f"Reading {filename}")
            grid_out = fs.get(file_id)
            process_upload_stream(grid_out, db, progress_callback)
            fs.delete(file_id)
            
            # Use TaskManager to add the task
            tm = TaskManager(db)
            tm.add_task('BUILD_INDEX')
            
        update_task_in_db(state='done', progress=100, info='Completed')
    except Exception as e:
        logging.exception(f"Error executing task {task_id}")
        update_task_in_db(state='failed', info=f"Error: {str(e)}")

@celery_app.task
def check_debounce_and_run_index_rebuild():
    db = get_db()
    task = db.tasks.find_one({'_id': INDEX_REBUILD_TASK_ID})
    if not task or task['state'] != 'pending rebuild':
        return

    elapsed = (datetime.now() - task['last_state_change']).total_seconds()
    if elapsed >= DEBOUNCE_SECONDS:
        # Try to delete it to ensure only one worker proceeds
        res = db.tasks.delete_one({'_id': INDEX_REBUILD_TASK_ID, 'state': 'pending rebuild'})
        if res.deleted_count > 0:
            # Create a real task
            real_task_id = str(uuid.uuid4())
            db.tasks.insert_one({
                '_id': real_task_id,
                'type': 'BUILD_INDEX',
                'state': 'scheduled',
                'progress': 0,
                'additional_info': 'Starting...',
                'created_at': datetime.now(),
                'last_state_change': datetime.now()
            })
            # Trigger Celery
            run_task_celery.delay(real_task_id, 'BUILD_INDEX')
    else:
        # Reschedule check
        remaining = max(0, DEBOUNCE_SECONDS - elapsed)
        check_debounce_and_run_index_rebuild.apply_async(countdown=int(remaining))

class TaskManager:
    def __init__(self, db):
        self.db = db
        self.fs = gridfs.GridFS(db)

    def add_task(self, task_type, data=None):
        if task_type == 'BUILD_INDEX':
            # Handle debounce in MongoDB
            self.db.tasks.update_one(
                {'_id': INDEX_REBUILD_TASK_ID},
                {
                    '$set': {
                        'type': 'BUILD_INDEX',
                        'state': 'pending rebuild',
                        'last_state_change': datetime.now(),
                    },
                    '$setOnInsert': {
                        'created_at': datetime.now(),
                        'progress': 0,
                        'additional_info': 'Debouncing...'
                    }
                },
                upsert=True
            )
            # Trigger a check with countdown
            check_debounce_and_run_index_rebuild.apply_async(countdown=DEBOUNCE_SECONDS)
            return INDEX_REBUILD_TASK_ID
        
        task_id = str(uuid.uuid4())
        task = {
            '_id': task_id,
            'type': task_type,
            'data': data,
            'state': 'scheduled',
            'progress': 0,
            'additional_info': 'Waiting in queue...',
            'created_at': datetime.now(),
            'last_state_change': datetime.now()
        }
        self.db.tasks.insert_one(task)
        run_task_celery.delay(task_id, task_type, data)
        return task_id

    def get_tasks(self, include_done=False, limit_done=0):
        if not include_done:
            # We'll get active tasks and optionally some done ones
            active_tasks = list(self.db.tasks.find({'state': {'$nin': ['done', 'failed']}}))
            if limit_done > 0:
                done_tasks = list(self.db.tasks.find({'state': {'$in': ['done', 'failed']}})
                                  .sort('last_state_change', -1).limit(limit_done))
                active_tasks.extend(done_tasks)
            tasks = active_tasks
        else:
            tasks = list(self.db.tasks.find({}))
        
        # Format for output (convert _id to id)
        for t in tasks:
            t['id'] = t.pop('_id')
            if t['id'] == INDEX_REBUILD_TASK_ID:
                elapsed = (datetime.now() - t['last_state_change']).total_seconds()
                remaining = max(0, int(DEBOUNCE_SECONDS - elapsed))
                t['additional_info'] = f"Debouncing... ({remaining}s left)"

        # Sort final list by created_at time
        tasks.sort(key=lambda t: t['created_at'])
        return tasks

    def get_index_state(self):
        # Look for active build index tasks
        active_build = self.db.tasks.find_one({
            'type': 'BUILD_INDEX',
            'state': {'$in': ['scheduled', 'work-in-progress']}
        })
        if active_build:
            return {
                "state": "building" if active_build['state'] == 'work-in-progress' else "pending rebuild",
                "last_state_change": active_build['last_state_change'].isoformat(),
                "additional_info": active_build['additional_info'],
                "progress": active_build['progress']
            }
        
        pending = self.db.tasks.find_one({'_id': INDEX_REBUILD_TASK_ID})
        if pending:
            elapsed = (datetime.now() - pending['last_state_change']).total_seconds()
            remaining = max(0, int(DEBOUNCE_SECONDS - elapsed))
            return {
                "state": "pending rebuild",
                "last_state_change": pending['last_state_change'].isoformat(),
                "additional_info": f"Debouncing... ({remaining}s left)",
                "progress": 0
            }
        
        return {
            "state": "up-to-date",
            "last_state_change": datetime.now().isoformat(),
            "additional_info": "",
            "progress": 100
        }
