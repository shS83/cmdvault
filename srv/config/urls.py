from django.db import connection
from django.http import JsonResponse
from django.urls import path
from accounts.views import Login
from devices.views import Logout, Devices, DeviceDetail
from synchronization.views import Push, Pull, Status


def health(request):
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
    except Exception:
        return JsonResponse({"status": "unavailable"}, status=503)
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("healthz", health),
    path("api/v1/auth/login", Login.as_view()),
    path("api/v1/auth/logout", Logout.as_view()),
    path("api/v1/devices", Devices.as_view()),
    path("api/v1/devices/<uuid:device_id>", DeviceDetail.as_view()),
    path("api/v1/sync/push", Push.as_view()),
    path("api/v1/sync/pull", Pull.as_view()),
    path("api/v1/sync/status", Status.as_view()),
]
