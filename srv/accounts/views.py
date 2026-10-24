import secrets
import time
from datetime import timedelta
from django.conf import settings
from django.contrib.auth import authenticate, get_user_model
from django.db import transaction
from django.utils import timezone
from django.utils.crypto import salted_hmac
from rest_framework.exceptions import AuthenticationFailed, Throttled, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from devices.models import Device, DeviceCredential
from devices.authentication import token_digest
from src.sync.protocol import SCHEMA_VERSION, validate_login
from .models import LoginBucket


def limit_login(request, username=None):
    ip = request.META.get("REMOTE_ADDR", "unknown")
    if settings.TRUST_PROXY:
        ip = request.META.get("HTTP_X_REAL_IP", ip)
    window = int(time.time()) // settings.LOGIN_WINDOW_SECONDS
    scope = "ip:" + ip if username is None else "account:" + username.casefold()
    key = salted_hmac("login-limit", scope).hexdigest()
    with transaction.atomic():
        LoginBucket.objects.get_or_create(key=key, defaults={"window": window})
        bucket = LoginBucket.objects.select_for_update().get(pk=key)
        if bucket.window != window:
            bucket.window, bucket.attempts = window, 0
        bucket.attempts = min(bucket.attempts + 1, settings.LOGIN_LIMIT + 1)
        bucket.save()
        blocked = bucket.attempts > settings.LOGIN_LIMIT
    if blocked:
        raise Throttled(wait=settings.LOGIN_WINDOW_SECONDS)


class Login(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    def get_authenticate_header(self, request):
        return "Bearer"

    def post(self, request):
        # Apply IP limiting before parsing, including malformed JSON.
        limit_login(request)
        data = request.data
        try:
            validate_login(data)
        except ValueError:
            raise ValidationError("invalid_login_request") from None
        limit_login(request, data["username"])
        user = authenticate(username=data["username"], password=data["password"])
        if user is None:
            raise AuthenticationFailed("invalid_credentials")
        with transaction.atomic():
            user = get_user_model().objects.select_for_update().get(pk=user.pk)
            if not user.is_active:
                raise AuthenticationFailed("invalid_credentials")
            device = Device.objects.create(owner=user, display_name=data["display_name"])
            token = secrets.token_urlsafe(32)
            expires = timezone.now() + timedelta(days=settings.DEVICE_TOKEN_DAYS)
            DeviceCredential.objects.create(device=device, hashed_token=token_digest(token), expires_at=expires)
        return Response({"schema_version": SCHEMA_VERSION, "device_id": str(device.pk),
                         "token": token, "expires_at": expires.isoformat()}, status=201)
