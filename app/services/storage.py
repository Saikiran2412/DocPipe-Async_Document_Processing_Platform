import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.config import settings

_client = boto3.client(
    "s3",
    endpoint_url=settings.s3_endpoint,
    aws_access_key_id=settings.s3_access_key,
    aws_secret_access_key=settings.s3_secret_key,
    region_name="us-east-1",
    config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
)


def ensure_bucket() -> None:
    try:
        _client.head_bucket(Bucket=settings.s3_bucket)
    except ClientError:
        _client.create_bucket(Bucket=settings.s3_bucket)


def presigned_upload_url(key: str, expires: int = 900) -> str:
    return _client.generate_presigned_url(
        "put_object",
        Params={"Bucket": settings.s3_bucket, "Key": key},
        ExpiresIn=expires,
    )


def object_exists(key: str) -> bool:
    try:
        _client.head_object(Bucket=settings.s3_bucket, Key=key)
        return True
    except ClientError:
        return False

def object_size(key: str) -> int:
    return _client.head_object(Bucket=settings.s3_bucket, Key=key)["ContentLength"]


def get_bytes(key: str) -> bytes:
    return _client.get_object(Bucket=settings.s3_bucket, Key=key)["Body"].read()


def put_bytes(key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
    _client.put_object(Bucket=settings.s3_bucket, Key=key, Body=data, ContentType=content_type)

def presigned_download_url(key: str, expires: int = 300) -> str:
    return _client.generate_presigned_url(
        "get_object", Params={"Bucket": settings.s3_bucket, "Key": key}, ExpiresIn=expires
    )
