import enum


class TaskType(enum.Enum):
    BUILD_INDEX = 'BUILD_INDEX'
    DELETE_DATE = 'DELETE_DATE'
    UPLOAD_DATA = 'UPLOAD_DATA'

class TaskState(enum.Enum):
    SCHEDULED = 'scheduled'
    WORK_IN_PROGRESS = 'work-in-progress'
    DONE = 'done'
    FAILED = 'failed'
