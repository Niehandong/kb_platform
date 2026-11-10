"""原始文件在 MinIO 中的存取。"""

import io

from app.common.clients import get_minio
from app.config import get_settings


def storage_key(kb_id: str, doc_id: str, generation: int, file_type: str) -> str:
    return f"kb/{kb_id}/{doc_id}/v{generation}.{file_type}"


def put_file(key: str, data: bytes) -> None:
    get_minio().put_object(get_settings().MINIO_BUCKET, key, io.BytesIO(data), length=len(data))


def get_file(key: str) -> bytes:
    response = get_minio().get_object(get_settings().MINIO_BUCKET, key)
    try:
        return response.read()
    finally:
        response.close()
        response.release_conn()


def remove_file(key: str) -> None:
    get_minio().remove_object(get_settings().MINIO_BUCKET, key)


def remove_prefix(prefix: str) -> int:
    """删除某个前缀下的所有文件，返回删除的个数。"""
    client, bucket = get_minio(), get_settings().MINIO_BUCKET
    count = 0
    for item in client.list_objects(bucket, prefix=prefix, recursive=True):
        client.remove_object(bucket, item.object_name)
        count += 1
    return count
