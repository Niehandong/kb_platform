"""账号、密钥、分组、知识库授权的数据表。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.common.db import Base, TimestampMixin, new_id

ACCOUNT_PERSON = "person"
ACCOUNT_SERVICE = "service"
STATUS_ACTIVE = "active"
STATUS_DISABLED = "disabled"

SUBJECT_GROUP = "group"
SUBJECT_ACCOUNT = "account"

LEVEL_READ = "read"
LEVEL_EDIT = "edit"
LEVEL_MANAGE = "manage"
# 档位从低到高，高档包含低档
LEVEL_RANK = {LEVEL_READ: 1, LEVEL_EDIT: 2, LEVEL_MANAGE: 3}


class Account(Base, TimestampMixin):
    __tablename__ = "account"
    __table_args__ = {"comment": "账号：个人账号可登录管理界面，服务账号只有密钥、供应用调用"}

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id, comment="主键")
    username: Mapped[str] = mapped_column(
        String(64), unique=True, comment="登录名，建议使用工号；服务账号为应用标识"
    )
    display_name: Mapped[str] = mapped_column(String(64), comment="显示名称")
    account_type: Mapped[str] = mapped_column(
        String(16), comment="账号类型：person 个人账号，service 服务账号"
    )
    password_hash: Mapped[str | None] = mapped_column(String(255), comment="密码哈希；服务账号为空")
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否平台管理员")
    can_create_kb: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否允许创建知识库")
    status: Mapped[str] = mapped_column(
        String(16), default=STATUS_ACTIVE, comment="状态：active 正常，disabled 停用"
    )
    must_change_password: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="下次登录是否必须修改密码"
    )
    token_valid_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="此时间之前签发的登录凭证一律失效；修改或重置密码时更新"
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="最近一次登录时间"
    )


class ApiKey(Base, TimestampMixin):
    __tablename__ = "api_key"
    __table_args__ = {"comment": "密钥：属于某个账号，代表该账号本人调用接口"}

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id, comment="主键")
    account_id: Mapped[str] = mapped_column(ForeignKey("account.id"), index=True, comment="所属账号")
    name: Mapped[str] = mapped_column(String(64), comment="密钥名称，由创建者填写，用于区分用途")
    key_prefix: Mapped[str] = mapped_column(
        String(12), comment="密钥前 12 位，用于在界面上辨认，不能用于鉴权"
    )
    key_hash: Mapped[str] = mapped_column(
        String(64), unique=True, comment="完整密钥的 SHA-256，鉴权时比对；不保存明文"
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="过期时间，空表示不过期"
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="吊销时间，非空表示已吊销"
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), comment="最近一次使用时间，精确到分钟级"
    )
    created_by: Mapped[str] = mapped_column(String(32), comment="创建者账号")


class UserGroup(Base, TimestampMixin):
    __tablename__ = "user_group"
    __table_args__ = {"comment": "分组：一批账号的集合，只用于批量授权，本身不带角色"}

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id, comment="主键")
    name: Mapped[str] = mapped_column(String(64), unique=True, comment="分组名称")
    description: Mapped[str] = mapped_column(Text, default="", comment="分组说明")


class GroupMember(Base):
    __tablename__ = "group_member"
    __table_args__ = (
        Index("ix_group_member_account_id", "account_id"),
        {"comment": "分组成员：账号属于分组"},
    )

    group_id: Mapped[str] = mapped_column(
        ForeignKey("user_group.id", ondelete="CASCADE"), primary_key=True, comment="分组"
    )
    account_id: Mapped[str] = mapped_column(ForeignKey("account.id"), primary_key=True, comment="账号")


class KbGrant(Base, TimestampMixin):
    __tablename__ = "kb_grant"
    __table_args__ = (
        UniqueConstraint("kb_id", "subject_type", "subject_id"),
        Index("ix_kb_grant_subject", "subject_type", "subject_id"),
        {"comment": "知识库授权：在某个知识库上授给分组或账号一个权限档位"},
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id, comment="主键")
    kb_id: Mapped[str] = mapped_column(ForeignKey("knowledge_base.id", ondelete="CASCADE"), comment="知识库")
    subject_type: Mapped[str] = mapped_column(String(16), comment="授权对象类型：group 分组，account 账号")
    subject_id: Mapped[str] = mapped_column(String(32), comment="分组或账号的主键")
    level: Mapped[str] = mapped_column(String(16), comment="权限档位：read 只读，edit 编辑，manage 管理")
    granted_by: Mapped[str] = mapped_column(String(32), comment="授权人账号")
