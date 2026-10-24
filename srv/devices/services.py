from contextlib import contextmanager
from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone
from rest_framework.exceptions import AuthenticationFailed
from .models import DeviceCredential


@contextmanager
def authorized(request):
    # All authenticated operations and revocations take the same owner lock.
    # Recheck the credential after waiting: a completed revocation wins.
    with transaction.atomic():
        owner = get_user_model().objects.select_for_update().get(pk=request.user.pk)
        credential = DeviceCredential.objects.select_related("device").get(pk=request.auth.pk)
        now = timezone.now()
        if (not owner.is_active or credential.revoked_at or credential.device.revoked_at
                or credential.expires_at <= now):
            raise AuthenticationFailed("invalid_device_token")
        credential.device.last_seen_at = now
        credential.device.save(update_fields=["last_seen_at"])
        yield owner, credential
