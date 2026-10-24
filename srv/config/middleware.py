from django.conf import settings
from django.core.exceptions import RequestDataTooBig
from django.http import JsonResponse
from rest_framework.views import exception_handler


def api_exception_handler(exc, context):
    if isinstance(exc, RequestDataTooBig):
        from rest_framework.response import Response
        return Response({"detail": "request_too_large"}, status=413)
    return exception_handler(exc, context)


class SafeBoundary:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Force Django's body limit before JSON parsing, also without Content-Length.
        try:
            if len(request.body) > settings.DATA_UPLOAD_MAX_MEMORY_SIZE:
                raise RequestDataTooBig()
        except RequestDataTooBig:
            return JsonResponse({"detail": "request_too_large"}, status=413)
        response = self.get_response(request)
        response["Cache-Control"] = "no-store"
        return response
