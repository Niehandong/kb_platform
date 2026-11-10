"""首次启动时创建平台管理员。"""

import logging
from pathlib import Path

from sqlalchemy import func, select

from app.audit.service import log_operation
from app.auth.models import ACCOUNT_PERSON, Account
from app.auth.security import generate_password, hash_password
from app.common.db import SessionLocal
from app.config import get_settings

logger = logging.getLogger(__name__)
INITIAL_ADMIN_USERNAME = "admin"


def ensure_initial_admin() -> None:
    """库里一个账号都没有时，创建 admin 并把初始密码写入文件。首次登录必须修改密码。"""
    with SessionLocal() as db:
        if db.scalar(select(func.count()).select_from(Account)):
            return
        password = generate_password(16)
        account = Account(
            username=INITIAL_ADMIN_USERNAME,
            display_name="平台管理员",
            account_type=ACCOUNT_PERSON,
            password_hash=hash_password(password),
            is_admin=True,
            can_create_kb=True,
            must_change_password=True,
        )
        db.add(account)
        db.flush()
        log_operation(
            db, None, "account.create", "account", account.id, detail={"username": account.username}
        )
        db.commit()

    path = Path(get_settings().SECRET_KEY_FILE).parent / "initial_admin_password"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"登录名：{INITIAL_ADMIN_USERNAME}\n初始密码：{password}\n首次登录后必须修改密码，修改后请删除本文件。\n",
        encoding="utf-8",
    )
    path.chmod(0o600)
    # 日志里只写文件位置，不写密码
    logger.info("已创建平台管理员 %s，初始密码保存在 %s", INITIAL_ADMIN_USERNAME, path.resolve())
