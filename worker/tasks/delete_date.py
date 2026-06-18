import time
from typing import Tuple

from shared.tasks import TaskState
from .base import BaseTask

class DeleteDateTask(BaseTask):
    def run(self) -> Tuple[TaskState, str]:
        start_time = time.time()

        date = self.data['date']
        self.update_progress(0, f"Preparing deletion for {date}")

        # Remove the activity entry for this date from all correlation items
        self.update_progress(10, f"Cleaning ip correlation tables for {date}...")
        self.db['correlated_rule_ip'].update_many(
            # Use $unset to remove the specific date key from activity-src and activity-dst
            {
                '$or': [
                    {f'activity-src.{date}': {'$exists': True}},
                    {f'activity-dst.{date}': {'$exists': True}}
                ]
            },
            {
                '$unset': {
                    f'activity-src.{date}': "",
                    f'activity-dst.{date}': ""
                }
            }
        )
        self.update_progress(20, f"Cleaning port correlation tables for {date}...")
        self.db['correlated_rule_ports'].update_many(
            {f'activity.{date}': {'$exists': True}},
            {'$unset': {f'activity.{date}': ""}}
        )

        self.update_progress(30, f"Deleting summaries and status for {date}...")
        delete_result = self.db['summaries'].delete_many({'date': date})
        self.db['data_status'].delete_one({'date': date})

        self.update_progress(100, "Done")
        elapsed = time.time() - start_time
        return TaskState.DONE, f"Deleted {delete_result.deleted_count} records and cleaned correlations in {int(elapsed)}s"
