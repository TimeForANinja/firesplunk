import logging
import time
from typing import Tuple

from shared.tasks import TaskState, TaskType
from .base import BaseTask


class BuildIndexTask(BaseTask):
    def run(self) -> Tuple[TaskState, str]:
        # a) check if we have a delete task scheduled
        delete_task = self.db.tasks.find_one({
            'type': TaskType.DELETE_DATE.value,
            'state': TaskState.SCHEDULED.value
        })
        # b) if so, skip this task
        if delete_task:
            return TaskState.DONE, "Skipped - delete task scheduled"

        # c) if not, wait for our 5m (300s) as a debounce then check again
        self.start_time = time.time()
        self.update_progress(0, "Debouncing (300s)...")
        time.sleep(300)
        
        # Check again
        delete_task = self.db.tasks.find_one({
            'type': TaskType.DELETE_DATE.value,
            'state': TaskState.SCHEDULED.value
        })
        if delete_task:
            return TaskState.DONE, "Skipped - additional delete task scheduled during debounce"

        self.update_progress(0, "Starting index build")

        self._init_data_status()
        self._build_ip_correlations()
        self._build_port_correlations()
        self._recreate_indexes()

        return TaskState.DONE, "Index rebuild complete"

    def _progress_callback(self, p: int, extra: str = ""):
        info = f"{p}%"
        elapsed = time.time() - self.start_time
        if p > 0:
            total_est = elapsed / (p / 100.0)
            remaining = total_est - elapsed
            if remaining > 60:
                info += f" - ~{int(remaining/60)}m left"
            else:
                info += f" - ~{int(remaining)}s left"
        if extra:
            info += f" ({extra})"
        self.update_progress(p, info)

    def _init_data_status(self):
        logging.info("Initializing data_status from existing summaries...")
        self._progress_callback(0, "Rebuilding correlation indexes")
        
        pipeline = [
            {'$group': {
                '_id': '$date',
                'count': {'$sum': '$count'},
                'uploaded_at': {'$first': '$uploaded_at'}
            }}
        ]
        results = list(self.db['summaries'].aggregate(pipeline))
        for res in results:
            self.db['data_status'].update_one(
                {'date': res['_id']},
                {'$set': {
                    'status': 'present',
                    'count': res['count'],
                    'uploaded_at': res['uploaded_at']
                }},
                upsert=True
            )
        self._progress_callback(30, "data_status rebuild done")

    def _build_ip_correlations(self):
        logging.info("Building IP correlations...")
        pipeline_corr = [
            {'$project': {
                '_id': 0,
                'rule': 1,
                'date': 1,
                'count': 1,
                '_dirs': [
                    {'ip': '$src_ip', 'type': 'src'},
                    {'ip': '$dest_ip', 'type': 'dst'},
                ],
            }},
            {'$unwind': '$_dirs'},
            {'$group': {
                '_id': {
                    'ip': '$_dirs.ip',
                    'rule': '$rule',
                    'type': '$_dirs.type',
                    'date': '$date',
                },
                'count': {'$sum': '$count'},
            }},
            {'$group': {
                '_id': {
                    'ip': '$_id.ip',
                    'rule': '$_id.rule',
                    'type': '$_id.type',
                },
                'activity': {'$push': {'k': '$_id.date', 'v': '$count'}},
            }},
            {'$group': {
                '_id': {
                    'ip': '$_id.ip',
                    'rule': '$_id.rule',
                },
                'activities': {'$push': {'k': '$_id.type', 'v': {'$arrayToObject': '$activity'}}}
            }},
            {'$project': {
                '_id': 0,
                'ip': '$_id.ip',
                'rule': '$_id.rule',
                'activity_data': {'$arrayToObject': '$activities'},
            }},
            {'$project': {
                'ip': 1,
                'rule': 1,
                'activity-src': '$activity_data.src',
                'activity-dst': '$activity_data.dst',
            }},
            {'$out': 'correlated_rule_ip'},
        ]
        self.db['summaries'].aggregate(pipeline_corr, allowDiskUse=True)
        self._progress_callback(60, "IP correlations done")

    def _build_port_correlations(self):
        logging.info("Building port correlations...")
        pipeline_ports = [
            {'$unwind': '$ports'},
            {'$group': {
                '_id': {'rule': '$rule', 'port': '$ports', 'date': '$date'},
                'count': {'$sum': '$count'},
            }},
            {'$group': {
                '_id': {'rule': '$_id.rule', 'port': '$_id.port'},
                'activity': {'$push': {'k': '$_id.date', 'v': '$count'}},
            }},
            {'$project': {
                '_id': 0,
                'rule': '$_id.rule',
                'port': '$_id.port',
                'activity': {'$arrayToObject': '$activity'},
            }},
            {'$out': 'correlated_rule_ports'},
        ]
        self.db['summaries'].aggregate(pipeline_ports, allowDiskUse=True)
        self._progress_callback(95, "Port correlations done")

    def _recreate_indexes(self):
        logging.info("Recreating indexes...")
        self.db['correlated_rule_ip'].create_index(
            [("ip", 1), ("rule", 1)]
        )
        self.db['correlated_rule_ports'].create_index([("rule", 1), ("port", 1)])
        self._progress_callback(100, "Done")
