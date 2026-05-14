"""Storage infrastructure namespace."""

from app.infrastructure.storage.mongo import MongoClientAdapter, get_client, get_database
from app.infrastructure.storage.s3 import S3StorageAdapter, StorageObjectNotFoundError

__all__ = [
    "MongoClientAdapter",
    "S3StorageAdapter",
    "StorageObjectNotFoundError",
    "get_client",
    "get_database",
]
