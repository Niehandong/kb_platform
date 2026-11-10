"""账号、密钥、分组接口的请求与响应结构。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

USERNAME_PATTERN = r"^[A-Za-z0-9_.@-]{2,64}$"


class AccountOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: str
    account_type: str
    is_admin: bool
    can_create_kb: bool
    status: str
    must_change_password: bool
    last_login_at: datetime | None
    created_at: datetime


class AccountBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    display_name: str
    account_type: str


class LoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=128)


class LoginOut(BaseModel):
    token: str
    expires_at: datetime
    account: AccountOut


class ChangePasswordIn(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=1, max_length=128)


class AccountCreateIn(BaseModel):
    username: str = Field(pattern=USERNAME_PATTERN, description="登录名，2 到 64 位字母、数字或 _ . @ -")
    display_name: str = Field(min_length=1, max_length=64)
    account_type: Literal["person", "service"] = "person"
    password: str | None = Field(
        default=None, min_length=1, max_length=128, description="个人账号的初始密码；不填则自动生成"
    )
    is_admin: bool = False
    can_create_kb: bool = False


class AccountCreateOut(BaseModel):
    account: AccountOut
    initial_password: str | None = Field(description="初始密码，只在此处返回一次；服务账号为空")


class AccountUpdateIn(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=64)
    is_admin: bool | None = None
    can_create_kb: bool | None = None
    status: Literal["active", "disabled"] | None = None


class ResetPasswordOut(BaseModel):
    new_password: str = Field(description="新密码，只在此处返回一次")


class AccountPage(BaseModel):
    total: int
    items: list[AccountOut]


class ImportedAccount(BaseModel):
    row: int
    username: str
    display_name: str
    initial_password: str


class ImportFailure(BaseModel):
    row: int
    username: str
    reason: str


class ImportOut(BaseModel):
    created: list[ImportedAccount]
    failed: list[ImportFailure]


class KeyCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    expires_at: datetime | None = Field(default=None, description="过期时间，不填表示不过期")


class KeyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    key_prefix: str
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None
    created_at: datetime


class KeyCreateOut(BaseModel):
    key: str = Field(description="密钥明文，只在此处返回一次，请立即保存")
    info: KeyOut


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=500)


class GroupUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)


class GroupOut(BaseModel):
    id: str
    name: str
    description: str
    member_count: int
    created_at: datetime


class GroupBrief(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str


class MembersIn(BaseModel):
    account_ids: list[str] = Field(min_length=1, max_length=500)


class DirectoryOut(BaseModel):
    accounts: list[AccountBrief]
    groups: list[GroupBrief]
