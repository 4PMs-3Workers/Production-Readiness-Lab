"""A deterministic S3 double; real HTTPS/store behavior is tested separately."""

import hashlib
import io

from botocore.exceptions import ClientError


def client_error(status):
    return ClientError(
        {"Error": {"Code": str(status)}, "ResponseMetadata": {"HTTPStatusCode": status}}, "fixture"
    )


class Store:
    def __init__(self):
        self.objects = {}
        self.calls = []
        self.failures = []

    def called(self, operation, request):
        self.calls.append((operation, request.get("Key", request.get("Prefix"))))
        if self.failures:
            raise self.failures.pop(0)

    def put_object(self, **request):
        self.called("put", request)
        key = request["Key"]
        if request.get("IfNoneMatch") == "*" and key in self.objects:
            raise client_error(412)
        if "IfMatch" in request and (
            key not in self.objects or request["IfMatch"] != self.etag(self.objects[key])
        ):
            raise client_error(412)
        value = request["Body"]
        self.objects[key] = value if isinstance(value, bytes) else value.read()
        return {}

    def get_object(self, **request):
        self.called("get", request)
        if request["Key"] not in self.objects:
            raise client_error(404)
        value = self.objects[request["Key"]]
        return dict(Body=io.BytesIO(value), ContentLength=len(value), ETag=self.etag(value))

    @staticmethod
    def etag(value):
        return '"' + hashlib.sha256(value).hexdigest() + '"'

    def list_objects_v2(self, **request):
        self.called("list", request)
        return dict(
            Contents=[
                dict(Key=key) for key in sorted(self.objects) if key.startswith(request["Prefix"])
            ],
            IsTruncated=False,
        )

    def delete_object(self, **request):
        self.called("delete", request)
        key = request["Key"]
        if (
            key in self.objects
            and "IfMatch" in request
            and request["IfMatch"] != self.etag(self.objects[key])
        ):
            raise client_error(412)
        self.objects.pop(key, None)
        return {}
