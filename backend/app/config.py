"""运行配置：全部来自环境变量，单独启动时从 backend/.env 读取。"""

import secrets
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # 接口进程
    API_HOST: str = "127.0.0.1"
    API_PORT: int = 8210

    # 日志
    LOG_DIR: str = "logs"
    LOG_LEVEL: str = "INFO"
    LOG_KEEP_DAYS: int = 30

    # PostgreSQL
    DB_HOST: str = "127.0.0.1"
    DB_PORT: int = 8201
    DB_USER: str = "kb_platform"
    DB_PASSWORD: str = ""
    DB_NAME: str = "kb_platform"

    # Redis（任务队列）
    REDIS_HOST: str = "127.0.0.1"
    REDIS_PORT: int = 8202
    REDIS_PASSWORD: str = ""
    REDIS_DB: int = 0

    # MinIO（原始文件）
    MINIO_ENDPOINT: str = "127.0.0.1:8203"
    MINIO_ACCESS_KEY: str = ""
    MINIO_SECRET_KEY: str = ""
    MINIO_BUCKET: str = "kb-platform"
    MINIO_SECURE: bool = False

    # Milvus
    MILVUS_URI: str = "http://127.0.0.1:8205"
    MILVUS_TOKEN: str = ""
    MILVUS_DB: str = "default"

    # 向量模型（OpenAI 兼容接口）
    EMBEDDING_URL: str = ""
    EMBEDDING_MODEL: str = "bge-m3"
    EMBEDDING_API_KEY: str = ""
    EMBEDDING_DIM: int = 1024
    EMBEDDING_BATCH_SIZE: int = 32
    EMBEDDING_MAX_RETRIES: int = 5

    # 登录凭证
    SECRET_KEY: str = ""
    SECRET_KEY_FILE: str = "run/secret_key"
    TOKEN_EXPIRE_HOURS: int = 12

    # 业务限制
    UPLOAD_MAX_SIZE_MB: int = 50
    RETRIEVAL_LOG_KEEP_DAYS: int = 30

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+psycopg://{quote(self.DB_USER)}:{quote(self.DB_PASSWORD)}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )

    def resolve_secret_key(self) -> str:
        """返回签名密钥；未配置时首次启动自动生成并保存到文件，之后复用。"""
        if self.SECRET_KEY:
            return self.SECRET_KEY
        path = Path(self.SECRET_KEY_FILE)
        if path.exists():
            return path.read_text(encoding="utf-8").strip()
        path.parent.mkdir(parents=True, exist_ok=True)
        key = secrets.token_urlsafe(48)
        path.write_text(key, encoding="utf-8")
        path.chmod(0o600)
        return key


@lru_cache
def get_settings() -> Settings:
    return Settings()
