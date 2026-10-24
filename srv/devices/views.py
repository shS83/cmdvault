from django.utils import timezone
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView
from .models import Device
from .services import authorized


class Logout(APIView):
    def post(self, request):
        with authorized(request) as (_, credential):
            credential.revoked_at = timezone.now()
            credential.save(update_fields=["revoked_at"])
        return Response(status=204)


class Devices(APIView):
    def get(self, request):
        with authorized(request) as (owner, _):
            rows = list(Device.objects.filter(owner=owner).order_by("created_at").values(
                "id", "display_name", "created_at", "last_seen_at", "revoked_at"))
        return Response({"schema_version": 1, "devices": rows})


class DeviceDetail(APIView):
    def delete(self, request, device_id):
        with authorized(request) as (owner, _):
            device = Device.objects.filter(owner=owner, pk=device_id).first()
            if device is None:
                raise NotFound()
            if device.revoked_at is None:
                device.revoked_at = timezone.now()
                device.save(update_fields=["revoked_at"])
                device.credentials.filter(revoked_at__isnull=True).update(revoked_at=device.revoked_at)
        return Response(status=204)
