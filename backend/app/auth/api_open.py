"""用密钥调用的接口（/open/v1）中与身份有关的部分。"""

from fastapi import APIRouter

from app.auth.deps import KeyAccount
from app.auth.models import Account
from app.auth.schemas import AccountBrief

router = APIRouter(tags=["召回接口"])


@router.get("/me", response_model=AccountBrief, summary="查看这把密钥代表的账号，可用于验证密钥是否有效")
def whoami(account: KeyAccount) -> Account:
    return account
