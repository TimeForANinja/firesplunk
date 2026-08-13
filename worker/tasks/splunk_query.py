import csv
import io
import json
import logging
import time
from datetime import datetime
from typing import Any, Tuple
from urllib.parse import urlparse

import gridfs
import splunklib.client as client

from shared.env import (
    get_splunk_password, get_splunk_poll_interval, get_splunk_port,
    get_splunk_server_url, get_splunk_username, get_splunk_verify_ssl,
)
from shared.tasks import TaskState, TaskType
from .base import BaseTask


class SplunkQueryTask(BaseTask):
    """Run a Splunk search, save CSV in GridFS, and queue its upload task."""

    CSV_FIELDS = ('src_ip', 'dest_ip', 'rule', 'date', 'count', 'dest_port')

    def run(self) -> Tuple[TaskState, str]:
        query = self.data.get('query')
        uid = self.data.get('uid')
        date = self.data.get('date')
        if not isinstance(query, str) or not query.strip():
            raise ValueError('SPLUNK_QUERY task requires a non-empty query')
        if not isinstance(uid, str) or not uid.strip():
            raise ValueError('SPLUNK_QUERY task requires a non-empty uid')

        self.update_progress(0, 'Connecting to Splunk...')
        service = self._connect()
        job = service.jobs.create(query)
        self.update_progress(10, f'Waiting for Splunk job {job.sid}...')
        while not job.is_done():
            self.update_progress(10, f'Waiting for Splunk job {job.sid}...')
            time.sleep(get_splunk_poll_interval())
        if hasattr(job, 'is_failed') and job.is_failed():
            raise RuntimeError(f'Splunk job {job.sid} failed')

        self.update_progress(75, 'Fetching Splunk results...')
        results = self._read_results(job)
        csv_data = self._to_csv(results, date)
        filename = f'splunk-{date or uid}.csv'
        file_id = gridfs.GridFS(self.db).put(
            csv_data.encode('utf-8'), filename=filename, content_type='text/csv'
        )
        upload_task_id = self._schedule_upload(str(file_id), filename, date)
        if date:
            self.db['data_status'].update_one(
                {'date': date}, {'$set': {'status': 'uploading'}}, upsert=True
            )
        self.update_progress(100, 'Splunk results saved; upload scheduled')
        return TaskState.DONE, f'Queued upload task {upload_task_id} for {len(results)} result(s)'

    def retry(self) -> Tuple[TaskState, str]:
        return self.run()

    @classmethod
    def _to_csv(cls, results: list[dict[str, Any]], date: str = None) -> str:
        output = io.StringIO(newline='')
        writer = csv.DictWriter(output, fieldnames=cls.CSV_FIELDS, extrasaction='ignore')
        writer.writeheader()
        for result in results:
            row = dict(result)
            if date and not row.get('date'):
                row['date'] = date
            writer.writerow(row)
        return output.getvalue()

    @staticmethod
    def _read_results(job) -> list[dict[str, Any]]:
        response = job.results(output_mode='json')
        payload = response.read()
        if isinstance(payload, bytes):
            payload = payload.decode('utf-8')
        parsed = json.loads(payload)
        if isinstance(parsed, dict):
            return parsed.get('results', [])
        if isinstance(parsed, list):
            return parsed
        raise ValueError('Splunk returned an unexpected results payload')

    @staticmethod
    def _connect():
        parsed_url = urlparse(get_splunk_server_url())
        host = parsed_url.hostname or parsed_url.path
        scheme = parsed_url.scheme or 'https'
        username = get_splunk_username()
        password = get_splunk_password()
        if not username or not password:
            raise ValueError('APP_SPLUNK_USERNAME and APP_SPLUNK_PASSWORD must be configured')
        return client.connect(
            host=host, port=get_splunk_port(), scheme=scheme,
            username=username, password=password, verify=get_splunk_verify_ssl(),
        )

    def _schedule_upload(self, file_id: str, filename: str, date: str = None) -> str:
        task_id = f'{self.task_id}-upload'
        now = datetime.now()
        self.db.tasks.insert_one({
            '_id': task_id,
            'type': TaskType.UPLOAD_DATA.value,
            'data': {'file_id': file_id, 'filename': filename, 'date': date},
            'state': TaskState.SCHEDULED.value,
            'progress': 0,
            'additional_info': 'Waiting for worker...',
            'created_at': now,
            'last_state_change': now,
        })
        return task_id
