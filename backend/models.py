from apiflask import Schema
from apiflask.fields import String, List, Integer, DateTime, Boolean, File
from marshmallow.fields import Nested

# Schemas
class SummaryItemSchema(Schema):
    date = String(required=True, metadata={'description': 'Date of the activity (YYYY-MM-DD)'})
    src_ip = String(required=True, metadata={'description': 'Source IP address'})
    dest_ip = String(required=True, metadata={'description': 'Destination IP address'})
    rule = String(required=True, metadata={'description': 'Firewall rule name'})
    count = Integer(required=True, metadata={'description': 'Number of hits'})
    ports = String(required=True, metadata={'description': 'Destination ports involved (comma-separated or range)'})
    timestamp = String(metadata={'description': 'Optional more granular timestamp'})

class UploadSchema(Schema):
    file = File(required=True, metadata={'description': 'CSV file containing activity records'})

class MissingDataSchema(Schema):
    date = String(metadata={'description': 'The date being checked'})
    status = String(metadata={'description': 'Status of data: "present", "missing", or "locked"'})
    count = Integer(metadata={'description': 'Total number of records for this date'})
    splunk_query = String(metadata={'description': 'Splunk query to retrieve missing data'})
    splunk_link = String(metadata={'description': 'Direct link to Splunk search'})
    uploaded_at = DateTime(metadata={'description': 'Timestamp when data was first uploaded'})
    is_locked = Boolean(metadata={'description': 'Whether the date is today and thus "locked" from being complete'})

class TimelinePointSchema(Schema):
    timestamp = String(metadata={'description': 'Date/Timestamp of the point'})
    count = Integer(metadata={'description': 'Aggregate hit count for this point'})
    has_data = Boolean(metadata={'description': 'Whether any data exists in the database for this date'})

class IPSearchHitSchema(Schema):
    rule = String(metadata={'description': 'Firewall rule name'})
    count = Integer(metadata={'description': 'Number of hits'})
    last_activity = String(metadata={'description': 'Date of last activity (YYYY-MM-DD)'})

class RuleSearchHitSchema(Schema):
    ip = String(metadata={'description': 'IP address'})
    count = Integer(metadata={'description': 'Number of hits'})
    last_activity = String(metadata={'description': 'Date of last activity (YYYY-MM-DD)'})

class IPSearchResultSchema(Schema):
    timeline = List(Nested(TimelinePointSchema), metadata={'description': 'Daily activity timeline'})
    src_hits = List(Nested(IPSearchHitSchema), metadata={'description': 'Rules hit when this IP was source'})
    dst_hits = List(Nested(IPSearchHitSchema), metadata={'description': 'Rules hit when this IP was destination'})
    warning = String(metadata={'description': 'Warning message about data completeness'})

class PortHitSchema(Schema):
    port = Integer(metadata={'description': 'Port number'})
    count = Integer(metadata={'description': 'Number of hits'})
    last_activity = String(metadata={'description': 'Date of last activity (YYYY-MM-DD)'})

class RuleSearchResultSchema(Schema):
    timeline = List(Nested(TimelinePointSchema), metadata={'description': 'Daily activity timeline'})
    ports = List(Nested(PortHitSchema), metadata={'description': 'Active ports for this rule'})
    active_sources = List(Nested(RuleSearchHitSchema), metadata={'description': 'Top source IPs for this rule'})
    active_destinations = List(Nested(RuleSearchHitSchema), metadata={'description': 'Top destination IPs for this rule'})
    warning = String(metadata={'description': 'Warning message about data completeness'})

class MissingDataResponseSchema(Schema):
    days = List(Nested(MissingDataSchema), metadata={'description': 'List of status records for the lookback period'})

class IndexStateSchema(Schema):
    state = String(metadata={'description': 'Current state of the index'})
    last_state_change = String(metadata={'description': 'Timestamp of last state change'})
    additional_info = String(metadata={'description': 'Additional info like progress or estimated time'})
    progress = Integer(metadata={'description': 'Progress percentage'})
    is_up_to_date = Boolean(metadata={'description': 'Whether the index is considered up-to-date'})

class TaskSchema(Schema):
    id = String(metadata={'description': 'Unique task ID'})
    type = String(metadata={'description': 'Task type (UPLOAD_DATA, DELETE_DATE, BUILD_INDEX)'})
    state = String(metadata={'description': 'Current state (scheduled, work-in-progress, done, failed)'})
    progress = Integer(metadata={'description': 'Progress percentage'})
    additional_info = String(metadata={'description': 'Additional info/status message'})
    created_at = DateTime(metadata={'description': 'Task creation timestamp'})
    last_state_change = DateTime(metadata={'description': 'Last state change timestamp'})

class TaskListSchema(Schema):
    tasks = List(Nested(TaskSchema), metadata={'description': 'List of active or recent tasks'})
