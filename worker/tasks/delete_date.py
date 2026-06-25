import time
from typing import Tuple

from shared.tasks import TaskState
from .base import BaseTask

class DeleteDateTask(BaseTask):
    def retry(self) -> Tuple[TaskState, str]:
        # we should be able to simply retry the whole thing
        return self.run()

    def run(self) -> Tuple[TaskState, str]:
        start_time = time.time()

        date = self.data['date']
        self.update_progress(0, f"Preparing deletion for {date}")

        # Remove the activity entry for this date from all correlation items
        self.update_progress(10, f"Cleaning ip correlation tables for {date}...")
        delete_date_data(self.db, date)

        self.update_progress(100, "Done")
        elapsed = time.time() - start_time
        return TaskState.DONE, f"Deleted records and cleaned correlations for {date} in {int(elapsed)}s"


def delete_date_data(db, date):
    db['correlated_rule_ip'].update_many(
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
    db['correlated_rule_ports'].update_many(
        {f'activity.{date}': {'$exists': True}},
        {'$unset': {f'activity.{date}': ""}}
    )

    db['summaries'].delete_many({'date': date})
    db['data_status'].delete_one({'date': date})
