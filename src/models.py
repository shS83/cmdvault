"""Local sync metadata, independent of terminal display and Bash history."""
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, Optional
from uuid import UUID, uuid4


class SyncState(str, Enum):
    LOCAL_ONLY = 'local_only'
    ELIGIBLE = 'sync_eligible'


def new_uuid() -> str:
    return str(uuid4())


def validate_uuid(value: str) -> str:
    try:
        if not isinstance(value, str) or str(UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError, TypeError) as error:
        raise ValueError('Expected a canonical UUID') from error
    return value


@dataclass(frozen=True)
class Record:
    uuid: str
    kind: str
    data: Optional[Dict[str, Any]]
    sync_state: SyncState
    revision: int
    deleted_at: Optional[str]
    created_at: str
    updated_at: str

    @property
    def deleted(self) -> bool:
        return self.deleted_at is not None


@dataclass(frozen=True)
class OutboxEvent:
    sequence: int
    event_id: str
    record_uuid: str
    revision: int
    operation: str
    created_at: str
    attempts: int
    error_code: Optional[str]
