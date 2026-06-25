from abc import ABC, abstractmethod
from typing import Tuple
from datetime import datetime

from shared.tasks import TaskState


class BaseTask(ABC):
    def __init__(self, db, task_id, data=None):
        self.db = db
        self.task_id = task_id
        self.data = data or {}

    @abstractmethod
    def run(self) -> Tuple[TaskState, str]:
        pass

    @abstractmethod
    def retry(self) -> Tuple[TaskState, str]:
        pass

    def update_progress(self, progress, additional_info=None):
        update_data = {
            'progress': progress,
            'last_heartbeat': datetime.now()
        }
        if additional_info:
            update_data['additional_info'] = additional_info

        self.db.tasks.update_one(
            {'_id': self.task_id},
            {'$set': update_data}
        )
