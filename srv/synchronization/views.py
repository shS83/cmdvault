import hashlib
import json
from django.conf import settings
from django.core import signing
from rest_framework.exceptions import APIException, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from devices.services import authorized
from src.sync.protocol import SCHEMA_VERSION, validate_push
from .models import ChangeLog, SyncRecord


class Conflict(APIException):
    status_code = 409
    default_detail = "revision_conflict"

    def __init__(self, detail=None):
        if isinstance(detail, dict):
            # Preserve the integer revision; DRF normally stringifies error leaves.
            self.detail = detail
        else:
            super().__init__(detail)


class SyncDisabled(APIException):
    status_code = 503
    default_detail = "sync_disabled_pending_client_security"


def enabled():
    if not settings.SYNC_ENABLED:
        raise SyncDisabled()


def head(owner):
    return ChangeLog.objects.filter(owner=owner).order_by("-sequence").values_list("sequence", flat=True).first() or 0


def cursor_for(owner, sequence):
    return signing.dumps({"v": SCHEMA_VERSION, "owner": owner.pk, "sequence": sequence}, salt="sync-cursor")


def read_cursor(owner, cursor):
    if not cursor:
        return 0
    try:
        value = signing.loads(cursor, salt="sync-cursor")
        if (value["v"] != SCHEMA_VERSION or value["owner"] != owner.pk
                or type(value["sequence"]) is not int or not 0 <= value["sequence"] <= head(owner)):
            raise ValueError()
        return value["sequence"]
    except (signing.BadSignature, ValueError, KeyError, TypeError):
        raise ValidationError("invalid_cursor") from None


def receipt(change):
    return {"change_id": str(change.change_id), "record_uuid": str(change.record_uuid),
            "revision": change.revision, "sequence": change.sequence}


class Push(APIView):
    def post(self, request):
        enabled()
        try:
            data = validate_push(request.data)
        except (ValueError, TypeError, AttributeError):
            raise ValidationError("invalid_push") from None
        results = []
        with authorized(request) as (owner, _):
            sequence = head(owner)
            for item in data["mutations"]:
                digest = hashlib.sha256(json.dumps(item, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                old = ChangeLog.objects.filter(owner=owner, change_id=item["change_id"]).first()
                if old:
                    if old.request_digest != digest:
                        raise Conflict("change_id_reused")
                    results.append(receipt(old))
                    continue
                record = SyncRecord.objects.filter(owner=owner, uuid=item["record_uuid"]).first()
                revision = record.revision if record else 0
                if item["base_revision"] != revision:
                    raise Conflict({"code": "revision_conflict", "record_uuid": item["record_uuid"],
                                    "current_revision": revision})
                revision += 1
                envelope = item["envelope"]
                SyncRecord.objects.update_or_create(owner=owner, uuid=item["record_uuid"], defaults={
                    "revision": revision, "deleted": item["operation"] == "delete",
                    "encrypted_payload": envelope, "key_version": envelope["key_version"] if envelope else None})
                sequence += 1
                change = ChangeLog.objects.create(owner=owner, sequence=sequence, change_id=item["change_id"],
                    request_digest=digest, record_uuid=item["record_uuid"], revision=revision,
                    operation=item["operation"], encrypted_payload=envelope)
                results.append(receipt(change))
        # Do not advance the client's pull cursor on push: other devices' changes may be unseen.
        return Response({"schema_version": SCHEMA_VERSION, "accepted": results})


class Pull(APIView):
    def get(self, request):
        enabled()
        try:
            if set(request.query_params) - {"schema_version", "cursor", "limit"}:
                raise ValueError()
            if request.query_params.get("schema_version") != str(SCHEMA_VERSION):
                raise ValueError()
            limit = int(request.query_params.get("limit", "100"))
            if not 1 <= limit <= 500 or len(request.query_params.get("cursor", "")) > 1024:
                raise ValueError()
        except (TypeError, ValueError):
            raise ValidationError("invalid_pull") from None
        with authorized(request) as (owner, _):
            start = read_cursor(owner, request.query_params.get("cursor"))
            rows = list(ChangeLog.objects.filter(owner=owner, sequence__gt=start).order_by("sequence")[:limit + 1])
            page = rows[:limit]
            changes = [dict(receipt(c), operation=c.operation, envelope=c.encrypted_payload,
                            timestamp=c.timestamp.isoformat()) for c in page]
            cursor = cursor_for(owner, page[-1].sequence if page else start)
        return Response({"schema_version": SCHEMA_VERSION, "changes": changes,
                         "cursor": cursor, "has_more": len(rows) > limit})


class Status(APIView):
    def get(self, request):
        with authorized(request) as (owner, _):
            result = {"schema_version": SCHEMA_VERSION, "sync_enabled": settings.SYNC_ENABLED,
                      "sequence": head(owner), "records": SyncRecord.objects.filter(owner=owner, deleted=False).count()}
        return Response(result)
