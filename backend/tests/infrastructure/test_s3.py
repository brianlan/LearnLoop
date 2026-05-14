from __future__ import annotations

from io import BytesIO
from uuid import UUID

import pytest
from botocore.exceptions import ClientError

from app.infrastructure.config.settings import Settings
from app.infrastructure.storage.s3 import S3StorageAdapter, StorageObjectNotFoundError


class FakeS3Client:
    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.buckets: set[str] = set()

    def put_object(self, *, Bucket: str, Key: str, Body, ContentType: str) -> None:
        data = Body.read() if hasattr(Body, "read") else Body
        self.objects[(Bucket, Key)] = bytes(data)

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, BytesIO]:
        try:
            payload = self.objects[(Bucket, Key)]
        except KeyError as exc:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "missing"}},
                "GetObject",
            ) from exc
        return {"Body": BytesIO(payload)}

    def delete_object(self, *, Bucket: str, Key: str) -> None:
        self.objects.pop((Bucket, Key), None)

    def head_bucket(self, *, Bucket: str) -> None:
        if Bucket not in self.buckets:
            raise ClientError(
                {"Error": {"Code": "404", "Message": "missing"}},
                "HeadBucket",
            )

    def create_bucket(self, *, Bucket: str, CreateBucketConfiguration=None) -> None:
        self.buckets.add(Bucket)


def test_s3_put_get_delete_round_trip() -> None:
    client = FakeS3Client()
    client.buckets.add("media")
    adapter = S3StorageAdapter(settings=Settings(), client=client)

    adapter.put_object("media", "users/1/images/test.png", b"payload", "image/png")

    assert adapter.get_object("media", "users/1/images/test.png") == b"payload"

    adapter.delete_object("media", "users/1/images/test.png")

    with pytest.raises(StorageObjectNotFoundError):
        adapter.get_object("media", "users/1/images/test.png")


def test_s3_bucket_bootstrap() -> None:
    client = FakeS3Client()
    adapter = S3StorageAdapter(settings=Settings(), client=client)

    created = adapter.ensure_bucket("media")

    assert created is True
    assert adapter.bucket_exists("media") is True
    assert adapter.ensure_bucket("media") is False


def test_s3_generates_backend_controlled_keys() -> None:
    adapter = S3StorageAdapter(
        settings=Settings(),
        client=FakeS3Client(),
        uuid_factory=lambda: UUID("12345678-1234-5678-1234-567812345678"),
    )

    key = adapter.build_object_key("user-42", ".jpg")

    assert key == "users/user-42/images/12345678-1234-5678-1234-567812345678.jpg"
