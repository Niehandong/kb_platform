"""Alembic 运行环境：连接信息取自应用配置，表结构取自模型。"""

from logging.config import fileConfig

from alembic import context

from app import models  # noqa: F401  导入后所有表才会登记到 Base.metadata
from app.common.db import Base, engine

if context.config.config_file_name:
    fileConfig(context.config.config_file_name)

target_metadata = Base.metadata


def run_migrations_online() -> None:
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise SystemExit("不支持离线模式，请直接连接数据库执行迁移")
run_migrations_online()
