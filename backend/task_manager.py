import threading
import time
import logging
import uuid
from datetime import datetime
import gridfs

from backend.db import build_correlations

DEBOUNCE_SECONDS = 300
INDEX_REBUILD_TASK_ID = "index-rebuild-scheduled"


class TaskManager:
    def __init__(self, db):
        self.db = db
        self.fs = gridfs.GridFS(db)
        self.tasks = {}  # task_id -> task_info
        self._lock = threading.Lock()
        self._worker_thread = None
        self._stop_event = threading.Event()
        
        self._start_worker()

    def _start_worker(self):
        self._worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self._worker_thread.start()

    def _worker_loop(self):
        while not self._stop_event.is_set():
            task_to_run = None

            with self._lock:
                # Check for scheduled tasks that can be started
                for task_id, task in self.tasks.items():
                    if task_id == INDEX_REBUILD_TASK_ID:
                        continue
                    if task['state'] == 'scheduled':
                        task_to_run = task
                        break

                # If no direct tasks, check if index rebuild debounce is over
                if not task_to_run and INDEX_REBUILD_TASK_ID in self.tasks:
                    task = self.tasks[INDEX_REBUILD_TASK_ID]
                    elapsed = (datetime.now() - task['last_state_change']).total_seconds()
                    if elapsed >= DEBOUNCE_SECONDS:
                        task_to_run = self._create_index_rebuild_task()

            if task_to_run:
                self._run_task(task_to_run)
            else:
                time.sleep(1)

    def _create_index_rebuild_task(self):
        # Internal helper to create the actual building task when debounce is over
        task_id = str(uuid.uuid4())
        task = {
            'id': task_id,
            'type': 'BUILD_INDEX',
            'state': 'scheduled',
            'progress': 0,
            'additional_info': 'Starting...',
            'created_at': datetime.now(),
            'last_state_change': datetime.now()
        }
        self.tasks[task_id] = task
        # Remove the dummy task
        self.tasks.pop(INDEX_REBUILD_TASK_ID, None)
        return task

    def add_task(self, task_type, data=None):
        with self._lock:
            if task_type == 'BUILD_INDEX':
                if INDEX_REBUILD_TASK_ID in self.tasks:
                    self.tasks[INDEX_REBUILD_TASK_ID]['last_state_change'] = datetime.now()
                else:
                    self.tasks[INDEX_REBUILD_TASK_ID] = {
                        'id': INDEX_REBUILD_TASK_ID,
                        'type': 'BUILD_INDEX',
                        'state': 'pending rebuild',
                        'progress': 0,
                        'additional_info': 'Debouncing...',
                        'created_at': datetime.now(),
                        'last_state_change': datetime.now()
                    }
                return INDEX_REBUILD_TASK_ID
            
            task_id = str(uuid.uuid4())
            task = {
                'id': task_id,
                'type': task_type,
                'data': data,
                'state': 'scheduled',
                'progress': 0,
                'additional_info': 'Waiting in queue...',
                'created_at': datetime.now(),
                'last_state_change': datetime.now()
            }
            self.tasks[task_id] = task
            return task_id

    def _update_task(self, task_id, state=None, progress=None, info=None):
        with self._lock:
            if task_id in self.tasks:
                if state:
                    self.tasks[task_id]['state'] = state
                if progress is not None:
                    self.tasks[task_id]['progress'] = progress
                if info is not None:
                    self.tasks[task_id]['additional_info'] = info
                self.tasks[task_id]['last_state_change'] = datetime.now()

    def _run_task(self, task):
        task_id = task['id']
        task_type = task['type']
        self._update_task(task_id, state='work-in-progress', info='Starting...')
        
        start_time = time.time()

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
            self._update_task(task_id, progress=p, info=info)

        try:
            if task_type == 'BUILD_INDEX':
                build_correlations(self.db, progress_callback=progress_callback)
            elif task_type == 'DELETE_DATE':
                self._execute_delete_date(task['data'], progress_callback)
            elif task_type == 'UPLOAD_DATA':
                self._execute_upload(task['data'], progress_callback)
            
            self._update_task(task_id, state='done', progress=100, info='Completed')
        except Exception as e:
            logging.exception(f"Error executing task {task_id}")
            self._update_task(task_id, state='failed', info=f"Error: {str(e)}")

    def _execute_delete_date(self, data, progress_callback):
        date = data['date']
        progress_callback(0, f"Deleting records for {date}")
        result = self.db['summaries'].delete_many({'date': date})
        progress_callback(50, f"Deleted {result.deleted_count} records. Updating status...")
        self.db['data_status'].delete_one({'date': date})
        progress_callback(90, "Requesting index rebuild")
        self.add_task('BUILD_INDEX')
        progress_callback(100, "Done")

    def _execute_upload(self, data, progress_callback):
        from utils import process_upload_stream
        file_id = data['file_id']
        filename = data['filename']
        
        progress_callback(0, f"Reading {filename}")
        grid_out = self.fs.get(file_id)
        
        # We need a way to pass the DB and other config to the processing function
        # For now, let's assume we can import it or it's a method here
        process_upload_stream(grid_out, self.db, progress_callback)
        
        # Cleanup GridFS
        self.fs.delete(file_id)
        
        self.add_task('BUILD_INDEX')

    def get_tasks(self, include_done=False, limit_done=0):
        with self._lock:
            if include_done:
                tasks = list(self.tasks.values())
            else:
                active_tasks = [t for t in self.tasks.values() if t['state'] not in ['done', 'failed']]
                if limit_done > 0:
                    done_tasks = [t for t in self.tasks.values() if t['state'] in ['done', 'failed']]
                    # Sort done tasks by last_state_change descending (most recent first)
                    done_tasks.sort(key=lambda t: t['last_state_change'], reverse=True)
                    active_tasks.extend(done_tasks[:limit_done])
                tasks = active_tasks
            
            # Sort final list by created_at time
            tasks.sort(key=lambda t: t['created_at'])

            # Inject debounce time-left if applicable
            for task in tasks:
                if task['id'] == INDEX_REBUILD_TASK_ID:
                    elapsed = (datetime.now() - task['last_state_change']).total_seconds()
                    remaining = max(0, int(DEBOUNCE_SECONDS - elapsed))
                    task['additional_info'] = f"Debouncing... ({remaining}s left)"

            return tasks

    def get_index_state(self):
        # For compatibility with old index state calls
        with self._lock:
            active_index_tasks = [t for t in self.tasks.values() if t['type'] == 'BUILD_INDEX' and t['state'] in ['scheduled', 'work-in-progress']]
            if active_index_tasks:
                task = active_index_tasks[0]
                return {
                    "state": "building" if task['state'] == 'work-in-progress' else "pending rebuild",
                    "last_state_change": task['last_state_change'].isoformat(),
                    "additional_info": task['additional_info'],
                    "progress": task['progress']
                }
            
            if INDEX_REBUILD_TASK_ID in self.tasks:
                task = self.tasks[INDEX_REBUILD_TASK_ID]
                elapsed = (datetime.now() - task['last_state_change']).total_seconds()
                remaining = max(0, int(DEBOUNCE_SECONDS - elapsed))
                return {
                    "state": "pending rebuild",
                    "last_state_change": task['last_state_change'].isoformat(),
                    "additional_info": f"Debouncing... ({remaining}s left)",
                    "progress": 0
                }
            
            return {
                "state": "up-to-date",
                "last_state_change": datetime.now().isoformat(), # Should probably track last successful build
                "additional_info": "",
                "progress": 100
            }
