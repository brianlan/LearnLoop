from base64 import b64encode
from collections.abc import Callable
from typing import Any, BinaryIO, Protocol, cast
from uuid import UUID, uuid4

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.infrastructure.config.settings import Settings, get_settings

BytesLike = bytes | bytearray | memoryview | BinaryIO
S3ClientFactory = Callable[..., object]
UUIDFactory = Callable[[], UUID]


class S3BodyReader(Protocol):
    def read(self) -> bytes | bytearray | memoryview: ...


class S3ClientProtocol(Protocol):
    def put_object(self, *, Bucket: str, Key: str, Body: BytesLike, ContentType: str) -> Any: ...

    def get_object(self, *, Bucket: str, Key: str) -> dict[str, Any]: ...

    def delete_object(self, *, Bucket: str, Key: str) -> Any: ...

    def head_bucket(self, *, Bucket: str) -> Any: ...

    def create_bucket(self, *, Bucket: str, CreateBucketConfiguration: object | None = None) -> Any: ...


class StorageObjectNotFoundError(FileNotFoundError):
    """Raised when an expected object is absent."""


class S3StorageAdapter:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: object | None = None,
        client_factory: S3ClientFactory | None = None,
        uuid_factory: UUIDFactory = uuid4,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client
        self._client_factory = client_factory or boto3.client
        self._uuid_factory = uuid_factory

    @property
    def client(self) -> S3ClientProtocol:
        if self._client is None:
            config = Config(
                region_name=self._settings.s3_region,
                s3={
                    "addressing_style": "path"
                    if self._settings.s3_force_path_style
                    else "virtual"
                },
            )
            self._client = self._client_factory(
                "s3",
                endpoint_url=self._settings.s3_endpoint,
                aws_access_key_id=self._settings.s3_access_key,
                aws_secret_access_key=self._settings.s3_secret_key,
                region_name=self._settings.s3_region,
                config=config,
            )
        return cast(S3ClientProtocol, self._client)

    def build_object_key(self, user_id: str, extension: str, *, category: str = "images") -> str:
        suffix = extension.lstrip(".") or "bin"
        return f"users/{user_id}/{category}/{self._uuid_factory()}.{suffix}"

    def put_object(self, bucket: str, key: str, data: BytesLike, content_type: str) -> None:
        self.client.put_object(
            Bucket=bucket,
            Key=key,
            Body=data,
            ContentType=content_type,
        )

    def get_object(self, bucket: str, key: str) -> bytes:
        try:
            response = self.client.get_object(Bucket=bucket, Key=key)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code in {"404", "NoSuchKey", "NotFound"}:
                raise StorageObjectNotFoundError(key) from exc
            raise

        body = response.get("Body")
        if body is None:
            raise StorageObjectNotFoundError(key)
        stream = cast(S3BodyReader, body)

        payload = stream.read()
        return payload if isinstance(payload, bytes) else bytes(payload)

    def delete_object(self, bucket: str, key: str) -> None:
        self.client.delete_object(Bucket=bucket, Key=key)

    def bucket_exists(self, bucket: str) -> bool:
        try:
            self.client.head_bucket(Bucket=bucket)
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code in {"404", "NoSuchBucket", "NotFound"}:
                return False
            raise
        return True

    def ensure_bucket(self, bucket: str) -> bool:
        if self.bucket_exists(bucket):
            return False

        try:
            if self._settings.s3_region == "us-east-1":
                self.client.create_bucket(Bucket=bucket)
            else:
                self.client.create_bucket(
                    Bucket=bucket,
                    CreateBucketConfiguration={
                        "LocationConstraint": self._settings.s3_region,
                    },
                )
        except ClientError as exc:
            error_code = exc.response.get("Error", {}).get("Code")
            if error_code not in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                raise
            return False
        except BotoCoreError:
            raise

        return True


def load_source_image_base64(
    source_image: dict[str, Any] | None,
    storage: S3StorageAdapter,
) -> str | None:
    """Load image from S3 and return as base64 string, or None if not found."""
    if not source_image:
        return None
    bucket = source_image.get("bucket")
    object_key = source_image.get("objectKey")
    if not bucket or not object_key:
        return None
    try:
        image_bytes = storage.get_object(str(bucket), str(object_key))
    except StorageObjectNotFoundError:
        return None
    return b64encode(image_bytes).decode("ascii")
