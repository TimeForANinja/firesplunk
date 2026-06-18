import uuid
from datetime import datetime
import gridfs

DEBOUNCE_SECONDS = 300

class TaskManager:
    def __init__(self, db):
        self.db = db
        self.fs = gridfs.GridFS(db)

    def add_build_index_task(self):
        # Handle debounce in MongoDB
        # We look for an existing 'pending rebuild' task
        task = self.db.tasks.find_one({
            'type': 'BUILD_INDEX',
            'state': 'pending rebuild'
        })

        if task:
            task_id = task['_id']
            self.db.tasks.update_one(
                {'_id': task_id},
                {
                    '$set': {
                        'last_state_change': datetime.now(),
                        'additional_info': 'Debouncing...'
                    }
                }
            )
        else:
            task_id = str(uuid.uuid4())
            self.db.tasks.insert_one({
                '_id': task_id,
                'type': 'BUILD_INDEX',
                'state': 'pending rebuild',
                'progress': 0,
                'additional_info': 'Debouncing...',
                'created_at': datetime.now(),
                'last_state_change': datetime.now()
            })
            
        return task_id

    def add_delete_date_task(self, date):
        task_id = str(uuid.uuid4())
        task = {
            '_id': task_id,
            'type': 'DELETE_DATE',
            'data': {'date': date},
            'state': 'scheduled',
            'progress': 0,
            'additional_info': 'Waiting in queue...',
            'created_at': datetime.now(),
            'last_state_change': datetime.now()
        }
        self.db.tasks.insert_one(task)
        return task_id

    def add_upload_data_task(self, file_id, filename):
        task_id = str(uuid.uuid4())
        task = {
            '_id': task_id,
            'type': 'UPLOAD_DATA',
            'data': {'file_id': file_id, 'filename': filename},
            'state': 'scheduled',
            'progress': 0,
            'additional_info': 'Waiting in queue...',
            'created_at': datetime.now(),
            'last_state_change': datetime.now()
        }
        self.db.tasks.insert_one(task)
        return task_id

    def get_tasks(self, limit_done=0):
        # We'll get active tasks and optionally some done ones
        active_tasks = list(self.db.tasks.find({'state': {'$nin': ['done', 'failed']}}))
        if limit_done > 0:
            done_tasks = list(self.db.tasks.find({'state': {'$in': ['done', 'failed']}})
                              .sort('last_state_change', -1).limit(limit_done))
            active_tasks.extend(done_tasks)
        tasks = active_tasks
        
        # Format for output (convert _id to id)
        for t in tasks:
            t['id'] = t.pop('_id')
            if t['type'] == 'BUILD_INDEX' and t['state'] == 'pending rebuild':
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
        
        pending = self.db.tasks.find_one({
            'type': 'BUILD_INDEX',
            'state': 'pending rebuild'
        })
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
