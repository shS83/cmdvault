import hashlib
import re
from django.utils import timezone
from rest_framework.authentication import BaseAuthentication, get_authorization_header
from rest_framework.exceptions import AuthenticationFailed
from .models import DeviceCredential


def token_digest(token):
    return hashlib.sha256(token.encode("ascii")).hexdigest()


class DeviceAuthentication(BaseAuthentication):
    def authenticate_header(self, request):
        return "Bearer"

    def authenticate(self, request):
        header = get_authorization_header(request)
        if not header:
            return None
        if not re.fullmatch(rb"Bearer [A-Za-z0-9_-]{43}", header):
            raise AuthenticationFailed("invalid_device_token")
        digest = token_digest(header[7:].decode("ascii"))
        credential = DeviceCredential.objects.select_related("device__owner").filter(
            hashed_token=digest, revoked_at__isnull=True, expires_at__gt=timezone.now(),
            device__revoked_at__isnull=True, device__owner__is_active=True).first()
        if credential is None:
            raise AuthenticationFailed("invalid_device_token")
        return credential.device.owner, credential
