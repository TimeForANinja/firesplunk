import uuid
from datetime import datetime, timedelta
from typing import List, Dict, Any
import gridfs
from pymongo.synchronous.database import Database

from shared.tasks import TaskType, TaskState


class TaskManager:
    def __init__(self, db: Database):
        self.db = db
        self.fs = gridfs.GridFS(db)

    def add_build_index_task(self) -> str:
        task_id = str(uuid.uuid4())
        self.db.tasks.insert_one({
            '_id': task_id,
            'type': TaskType.BUILD_INDEX.value,
            'data': {},
            'state': TaskState.SCHEDULED.value,
            'progress': 0,
            'additional_info': 'Requested',
            'created_at': datetime.now(),
            'last_state_change': datetime.now()
        })
        return task_id

    def add_delete_date_task(self, date: str) -> str:
        task_id = str(uuid.uuid4())
        self.db.tasks.insert_one({
            '_id': task_id,
            'type': TaskType.DELETE_DATE.value,
            'data': {'date': date},
            'state': TaskState.SCHEDULED.value,
            'progress': 0,
            'additional_info': 'Waiting for worker...',
            'created_at': datetime.now(),
            'last_state_change': datetime.now()
        })
        return task_id

    def add_upload_data_task(self, file_id: str, filename: str) -> str:
        task_id = str(uuid.uuid4())
        self.db.tasks.insert_one({
            '_id': task_id,
            'type': TaskType.UPLOAD_DATA.value,
            'data': {'file_id': file_id, 'filename': filename},
            'state': TaskState.SCHEDULED.value,
            'progress': 0,
            'additional_info': 'Waiting for worker...',
            'created_at': datetime.now(),
            'last_state_change': datetime.now()
        })
        return task_id

    def retry_task(self, task_id: str):
        original_task = self.db.tasks.find_one({'_id': task_id})
        if not original_task:
            return

        new_task_id = str(uuid.uuid4())
        # Set created_at to just after the original task to ensure it's "underneath" in UI 
        # and worked on before any subsequent regular tasks (as it's still older than them).
        new_created_at = original_task['created_at'] + timedelta(seconds=1)

        self.db.tasks.insert_one({
            '_id': new_task_id,
            'type': original_task['type'],
            'data': original_task['data'],
            'state': TaskState.SCHEDULED.value,
            'progress': 0,
            'additional_info': 'Retrying...',
            'created_at': new_created_at,
            'last_state_change': datetime.now(),
            'is_retry': True
        })

    def get_tasks(self, limit_done: int = 0) -> List[Dict[str, Any]]:
        # We'll get active tasks and optionally some done ones
        active_tasks = list(self.db.tasks.find({'state': {'$nin': [TaskState.DONE.value, TaskState.FAILED.value]}}))
        if limit_done > 0:
            done_tasks = list(self.db.tasks.find({'state': {'$in': [TaskState.DONE.value, TaskState.FAILED.value]}})
                              .sort('last_state_change', -1).limit(limit_done))
            active_tasks.extend(done_tasks)
        tasks = active_tasks

        # Format for output (convert _id to id)
        for t in tasks:
            t['id'] = t.pop('_id')

        # Sort final list by created_at time
        tasks.sort(key=lambda t: t['created_at'])
        return tasks
