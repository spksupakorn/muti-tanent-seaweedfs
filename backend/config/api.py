import logging
from uuid import uuid4

from django.db import connection
from ninja import NinjaAPI

from storage import client

log = logging.getLogger(__name__)

api = NinjaAPI(title="File storage API")


@api.get("/health")
def health(request):
    """DB reachable and the S3 identity can write, read and delete in its bucket."""
    try:
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        key = f"_health/{uuid4()}"
        client.put(key, b"ok")
        if client.get(key) != b"ok":
            raise RuntimeError("storage read-back mismatch")
        client.delete(key)
    except Exception:
        log.exception("health check failed")
        return api.create_response(request, {"status": "error"}, status=503)
    return {"status": "ok"}
