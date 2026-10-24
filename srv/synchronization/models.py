from django.conf import settings
from django.db import models


class SyncRecord(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    uuid = models.UUIDField()
    encrypted_payload = models.JSONField(null=True)
    key_version = models.PositiveIntegerField(null=True)
    revision = models.PositiveBigIntegerField()
    deleted = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "uuid"], name="unique_owner_record"),
            models.CheckConstraint(condition=models.Q(revision__gte=1), name="positive_record_revision"),
            models.CheckConstraint(condition=(
                models.Q(deleted=True, encrypted_payload__isnull=True, key_version__isnull=True) |
                models.Q(deleted=False, encrypted_payload__isnull=False, key_version__gte=1)),
                name="record_envelope_or_tombstone"),
        ]


class ChangeLog(models.Model):
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    sequence = models.PositiveBigIntegerField()
    change_id = models.UUIDField()
    request_digest = models.CharField(max_length=64)
    record_uuid = models.UUIDField()
    revision = models.PositiveBigIntegerField()
    operation = models.CharField(max_length=6, choices=[("upsert", "upsert"), ("delete", "delete")])
    # Immutable encrypted snapshot: pull must never pair an old revision with new ciphertext.
    encrypted_payload = models.JSONField(null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["owner", "sequence"], name="unique_owner_sequence"),
            models.UniqueConstraint(fields=["owner", "change_id"], name="unique_owner_change"),
            models.UniqueConstraint(fields=["owner", "record_uuid", "revision"], name="unique_record_revision"),
            models.CheckConstraint(condition=models.Q(sequence__gte=1, revision__gte=1), name="positive_change_counters"),
            models.CheckConstraint(condition=(models.Q(operation="upsert", encrypted_payload__isnull=False) |
                                              models.Q(operation="delete", encrypted_payload__isnull=True)),
                                   name="change_envelope_or_tombstone"),
        ]
