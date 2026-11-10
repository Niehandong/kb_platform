"""外部组件的连接：Redis、任务队列、MinIO、Milvus。都是进程内单例，首次使用时创建。"""

from functools import lru_cache

from minio import Minio
from pymilvus import MilvusClient
from redis import Redis
from rq import Queue

from app.config import get_settings

QUEUE_NAME = "kb_platform"


@lru_cache
def get_redis() -> Redis:
    s = get_settings()
    return Redis(
        host=s.REDIS_HOST,
        port=s.REDIS_PORT,
        password=s.REDIS_PASSWORD or None,
        db=s.REDIS_DB,
        socket_connect_timeout=5,
    )


@lru_cache
def get_queue() -> Queue:
    return Queue(QUEUE_NAME, connection=get_redis())


@lru_cache
def get_minio() -> Minio:
    s = get_settings()
    return Minio(
        s.MINIO_ENDPOINT, access_key=s.MINIO_ACCESS_KEY, secret_key=s.MINIO_SECRET_KEY, secure=s.MINIO_SECURE
    )


def ensure_bucket() -> None:
    """存原始文件的桶不存在时创建。"""
    client, bucket = get_minio(), get_settings().MINIO_BUCKET
    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)


@lru_cache
def get_milvus() -> MilvusClient:
    s = get_settings()
    return MilvusClient(uri=s.MILVUS_URI, token=s.MILVUS_TOKEN, timeout=10)
