import uuid
from datetime import datetime
from typing import List, Dict, Any
import gridfs
from pymongo.synchronous.database import Database

from shared.tasks import TaskType, TaskState

DEBOUNCE_SECONDS = 300


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

    def get_index_state(self) -> Dict[str, Any]:
        """Get the state of the most recent build index task and determine if it's up-to-date."""
        # Logic for is_up_to_date:
        # consider the index up-to-date if no task is scheduled / running / recently failed
        # else consider it out-of-date

        # Check for any scheduled or running tasks
        active_task = self.db.tasks.find_one({
            'state': {'$in': [TaskState.SCHEDULED.value, TaskState.WORK_IN_PROGRESS.value]}
        })

        # Check for recently failed tasks
        # For simplicity, "recently" is defined as within the last 24 hours.
        import datetime as dt
        recent_threshold = dt.datetime.now() - dt.timedelta(hours=24)
        failed_task = self.db.tasks.find_one({
            'state': TaskState.FAILED.value,
            'last_state_change': {'$gt': recent_threshold}
        })

        is_up_to_date = not (active_task or failed_task)

        # Look for active build index tasks
        active_build = self.db.tasks.find_one({
            'type': TaskType.BUILD_INDEX.value,
            'state': {'$in': [TaskState.SCHEDULED.value, TaskState.WORK_IN_PROGRESS.value]}
        })
        if active_build:
            return {
                "state": "building" if active_build['state'] == TaskState.WORK_IN_PROGRESS.value else "pending rebuild",
                "last_state_change": active_build['last_state_change'].isoformat(),
                "additional_info": active_build['additional_info'],
                "progress": active_build['progress'],
                "is_up_to_date": is_up_to_date
            }

        pending = self.db.tasks.find_one({
            'type': TaskType.BUILD_INDEX.value,
            'state': 'pending rebuild'
        })
        if pending:
            elapsed = (dt.datetime.now() - pending['last_state_change']).total_seconds()
            remaining = max(0, int(DEBOUNCE_SECONDS - elapsed))
            return {
                "state": "pending rebuild",
                "last_state_change": pending['last_state_change'].isoformat(),
                "additional_info": f"Debouncing... ({remaining}s left)",
                "progress": 0,
                "is_up_to_date": is_up_to_date
            }

        return {
            "state": "up-to-date",
            "last_state_change": dt.datetime.now().isoformat(),
            "additional_info": "",
            "progress": 100,
            "is_up_to_date": is_up_to_date
        }
