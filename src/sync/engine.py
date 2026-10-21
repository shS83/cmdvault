"""Local preparation boundary for a future encrypted sync client; no transport."""
from dataclasses import dataclass
import json
from typing import Optional
from ..models import OutboxEvent, SyncState
from ..security.crypto import EncryptedPayload, PayloadCipher
from ..security.secrets import SecretScanner
from .store import LocalStore


@dataclass(frozen=True)
class PreparedChange:
    event: OutboxEvent
    associated_data: bytes
    payload: Optional[EncryptedPayload]


def prepare_change(store: LocalStore, event: OutboxEvent, *,
                   scanner: SecretScanner, cipher: PayloadCipher) -> PreparedChange:
    """No implicit scanner or identity cipher; deleted payloads are never read.

    Preparation does not acknowledge events. A future client must acknowledge
    their event IDs only after a successful remote transaction.
    """
    current = store.connection.execute('SELECT event_id,revision,operation FROM cv_outbox WHERE record_uuid=?',
                                       (event.record_uuid,)).fetchone()
    record = store.get(event.record_uuid)
    if (not current or tuple(current) != (event.event_id,event.revision,event.operation)
            or record.revision != event.revision):
        raise ValueError('Outbox event has been superseded')
    aad = json.dumps({'event_id':event.event_id,'uuid':event.record_uuid,
                      'revision':event.revision,'kind':record.kind,'operation':event.operation},
                     sort_keys=True,separators=(',',':')).encode('utf-8')
    if event.operation == 'delete':
        return PreparedChange(event,aad,None)
    if record.deleted or record.sync_state != SyncState.ELIGIBLE:
        raise ValueError('Record is not sync eligible')
    result = scanner.scan(record.data)
    if result.allowed is not True or result.findings:
        raise ValueError('Record was not approved for synchronization')
    plaintext = json.dumps(record.data,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode('utf-8')
    payload = cipher.encrypt(plaintext,associated_data=aad)
    if (not isinstance(payload,EncryptedPayload) or not isinstance(payload.ciphertext,bytes)
            or not payload.ciphertext or not isinstance(payload.algorithm,str) or not payload.algorithm
            or not isinstance(payload.key_id,str) or not payload.key_id):
        raise ValueError('Cipher did not return an encrypted envelope')
    return PreparedChange(event,aad,payload)
