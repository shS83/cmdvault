import uuid
from django.conf import settings
from django.db import models


class Device(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    display_name = models.CharField(max_length=100)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True)
    revoked_at = models.DateTimeField(null=True)


class DeviceCredential(models.Model):
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name="credentials")
    hashed_token = models.CharField(max_length=64, unique=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True)
