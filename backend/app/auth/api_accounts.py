"""账号管理（平台管理员）：创建、批量导入、修改、停用、重置密码、管理密钥。"""

import io
import re
from typing import Annotated, Literal

from fastapi import APIRouter, File, Query, UploadFile
from fastapi.responses import Response
from openpyxl import Workbook, load_workbook
from sqlalchemy import func, or_, select

from app.audit.service import log_operation
from app.auth import keys
from app.auth.deps import AdminAccount, CurrentAccount, Db
from app.auth.models import (
    ACCOUNT_PERSON,
    ACCOUNT_SERVICE,
    STATUS_DISABLED,
    Account,
    GroupMember,
    UserGroup,
)
from app.auth.schemas import (
    USERNAME_PATTERN,
    AccountBrief,
    AccountCreateIn,
    AccountCreateOut,
    AccountOut,
    AccountPage,
    AccountUpdateIn,
    DirectoryOut,
    GroupBrief,
    ImportedAccount,
    ImportFailure,
    ImportOut,
    KeyCreateIn,
    KeyCreateOut,
    KeyOut,
    ResetPasswordOut,
)
from app.auth.security import generate_password, hash_password, next_cutoff
from app.common.errors import AppError

router = APIRouter(tags=["账号管理"])

IMPORT_HEADERS = ["登录名", "显示名称", "初始密码", "分组"]
IMPORT_MAX_ROWS = 1000
_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _get_account(db: Db, account_id: str) -> Account:
    account = db.get(Account, account_id)
    if account is None:
        raise AppError(404, "NOT_FOUND", "账号不存在")
    return account


def _like(keyword: str) -> str:
    escaped = keyword.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


@router.get("/accounts", response_model=AccountPage, summary="账号列表")
def list_accounts(
    _: AdminAccount,
    db: Db,
    keyword: str = "",
    account_type: Literal["person", "service"] | None = None,
    status: Literal["active", "disabled"] | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
) -> AccountPage:
    query = select(Account)
    if keyword.strip():
        pattern = _like(keyword.strip())
        query = query.where(or_(Account.username.ilike(pattern), Account.display_name.ilike(pattern)))
    if account_type:
        query = query.where(Account.account_type == account_type)
    if status:
        query = query.where(Account.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    items = db.scalars(
        query.order_by(Account.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    return AccountPage(total=total, items=[AccountOut.model_validate(a) for a in items])


@router.post("/accounts", response_model=AccountCreateOut, summary="创建账号，初始密码只返回这一次")
def create_account(body: AccountCreateIn, admin: AdminAccount, db: Db) -> AccountCreateOut:
    if db.scalar(select(Account.id).where(Account.username == body.username)):
        raise AppError(409, "USERNAME_EXISTS", "登录名已存在")

    password: str | None = None
    if body.account_type == ACCOUNT_PERSON:
        password = body.password or generate_password()
    elif body.password or body.is_admin:
        raise AppError(400, "INVALID_PARAM", "服务账号不能设置密码，也不能是平台管理员")

    account = Account(
        username=body.username,
        display_name=body.display_name.strip(),
        account_type=body.account_type,
        password_hash=hash_password(password) if password else None,
        is_admin=body.is_admin,
        can_create_kb=body.can_create_kb,
        must_change_password=password is not None,
    )
    db.add(account)
    db.flush()
    log_operation(
        db,
        admin.id,
        "account.create",
        "account",
        account.id,
        detail={
            "username": account.username,
            "account_type": account.account_type,
            "is_admin": account.is_admin,
        },
    )
    db.commit()
    return AccountCreateOut(account=AccountOut.model_validate(account), initial_password=password)


@router.get("/accounts/import-template", summary="下载批量导入模板")
def import_template(_: AdminAccount) -> Response:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "账号"
    sheet.append(IMPORT_HEADERS)
    sheet.append(["10086", "张三", "", "划线机;调阻机"])
    for column, width in zip("ABCD", (18, 18, 18, 30), strict=True):
        sheet.column_dimensions[column].width = width
    buffer = io.BytesIO()
    workbook.save(buffer)
    headers = {"Content-Disposition": 'attachment; filename="account-import-template.xlsx"'}
    return Response(buffer.getvalue(), media_type=_XLSX, headers=headers)


@router.post(
    "/accounts/import",
    response_model=ImportOut,
    summary="用 Excel 批量创建个人账号",
    description="表头为：登录名、显示名称、初始密码（可空，空则自动生成）、分组（可空，多个用分号分隔，分组必须已存在）。"
    "逐行处理，出错的行跳过并在结果中说明，不影响其他行。",
)
async def import_accounts(admin: AdminAccount, db: Db, file: Annotated[UploadFile, File()]) -> ImportOut:
    content = await file.read()
    try:
        sheet = load_workbook(io.BytesIO(content), read_only=True, data_only=True).active
        rows = list(sheet.iter_rows(values_only=True))
    except Exception as exc:
        raise AppError(400, "INVALID_FILE", "无法读取文件，请使用 xlsx 格式的导入模板") from exc

    def cell(row: tuple, index: int) -> str:
        value = row[index] if index < len(row) else None
        # 工号在 Excel 里常被存成数字，10086.0 应还原为 10086
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        return "" if value is None else str(value).strip()

    if not rows or [cell(rows[0], i) for i in range(4)] != IMPORT_HEADERS:
        raise AppError(400, "INVALID_FILE", "表头不正确，应为：" + "、".join(IMPORT_HEADERS))
    data_rows = [(n, r) for n, r in enumerate(rows[1:], start=2) if any(cell(r, i) for i in range(4))]
    if len(data_rows) > IMPORT_MAX_ROWS:
        raise AppError(400, "INVALID_FILE", f"一次最多导入 {IMPORT_MAX_ROWS} 行")

    groups = {g.name: g.id for g in db.scalars(select(UserGroup))}
    existing = set(db.scalars(select(Account.username)))
    created: list[ImportedAccount] = []
    failed: list[ImportFailure] = []

    for row_number, row in data_rows:
        username, display_name, password, group_text = (cell(row, i) for i in range(4))
        group_names = [g.strip() for g in re.split(r"[;；]", group_text) if g.strip()]
        unknown = [g for g in group_names if g not in groups]

        reason = None
        if not re.fullmatch(USERNAME_PATTERN, username):
            reason = "登录名应为 2 到 64 位字母、数字或 _ . @ -"
        elif username in existing:
            reason = "登录名已存在"
        elif not display_name or len(display_name) > 64:
            reason = "显示名称不能为空，且不超过 64 个字"
        elif unknown:
            reason = "分组不存在：" + "、".join(unknown)
        if reason:
            failed.append(ImportFailure(row=row_number, username=username, reason=reason))
            continue

        password = password or generate_password()
        account = Account(
            username=username,
            display_name=display_name,
            account_type=ACCOUNT_PERSON,
            password_hash=hash_password(password),
            must_change_password=True,
        )
        db.add(account)
        db.flush()
        for name in dict.fromkeys(group_names):
            db.add(GroupMember(group_id=groups[name], account_id=account.id))
        existing.add(username)
        created.append(
            ImportedAccount(
                row=row_number, username=username, display_name=display_name, initial_password=password
            )
        )

    log_operation(
        db,
        admin.id,
        "account.import",
        "account",
        admin.id,
        detail={"created": [c.username for c in created], "failed_count": len(failed)},
    )
    db.commit()
    return ImportOut(created=created, failed=failed)


@router.patch(
    "/accounts/{account_id}", response_model=AccountOut, summary="修改账号：名称、管理员、建库权限、启用停用"
)
def update_account(account_id: str, body: AccountUpdateIn, admin: AdminAccount, db: Db) -> Account:
    account = _get_account(db, account_id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if account.id == admin.id and ("is_admin" in changes or "status" in changes):
        # 不允许改自己，保证平台上始终至少有一个可用的管理员
        raise AppError(400, "CANNOT_MODIFY_SELF", "不能修改自己的管理员身份或启用状态")
    if account.account_type == ACCOUNT_SERVICE and changes.get("is_admin"):
        raise AppError(400, "INVALID_PARAM", "服务账号不能是平台管理员")

    before = {name: getattr(account, name) for name in changes}
    for name, value in changes.items():
        setattr(account, name, value.strip() if isinstance(value, str) and name == "display_name" else value)
    if changes.get("status") == STATUS_DISABLED:
        account.token_valid_after = next_cutoff(account.token_valid_after)
    if changes:
        log_operation(
            db, admin.id, "account.update", "account", account.id, detail={"before": before, "after": changes}
        )
        db.commit()
    return account


@router.post(
    "/accounts/{account_id}/reset-password",
    response_model=ResetPasswordOut,
    summary="重置密码，新密码只返回这一次",
)
def reset_password(account_id: str, admin: AdminAccount, db: Db) -> ResetPasswordOut:
    account = _get_account(db, account_id)
    if account.account_type != ACCOUNT_PERSON:
        raise AppError(400, "INVALID_PARAM", "服务账号没有密码")
    password = generate_password()
    account.password_hash = hash_password(password)
    account.must_change_password = True
    account.token_valid_after = next_cutoff(account.token_valid_after)
    log_operation(
        db, admin.id, "account.reset_password", "account", account.id, detail={"username": account.username}
    )
    db.commit()
    return ResetPasswordOut(new_password=password)


@router.get("/accounts/{account_id}/keys", response_model=list[KeyOut], summary="查看某个账号的密钥")
def list_account_keys(account_id: str, _: AdminAccount, db: Db) -> list:
    return keys.list_keys(db, _get_account(db, account_id).id)


@router.post(
    "/accounts/{account_id}/keys", response_model=KeyCreateOut, summary="为服务账号创建密钥，明文只返回这一次"
)
def create_account_key(account_id: str, body: KeyCreateIn, admin: AdminAccount, db: Db) -> KeyCreateOut:
    account = _get_account(db, account_id)
    if account.account_type != ACCOUNT_SERVICE:
        # 个人账号的密钥代表本人，只能由本人创建
        raise AppError(400, "INVALID_PARAM", "只能为服务账号创建密钥，个人账号的密钥由本人创建")
    return keys.create_key(db, owner=account, operator=admin, body=body)


@router.delete("/accounts/{account_id}/keys/{key_id}", response_model=KeyOut, summary="吊销某个账号的密钥")
def revoke_account_key(account_id: str, key_id: str, admin: AdminAccount, db: Db):
    return keys.revoke_key(db, owner_id=_get_account(db, account_id).id, key_id=key_id, operator=admin)


@router.get(
    "/directory",
    response_model=DirectoryOut,
    summary="按关键字查找账号和分组，供授权时选择",
    description="所有已登录账号可用，只返回授权所需的最少信息，每类最多 20 条。",
)
def directory(_: CurrentAccount, db: Db, keyword: str = "") -> DirectoryOut:
    account_query = select(Account).where(Account.status != STATUS_DISABLED)
    group_query = select(UserGroup)
    if keyword.strip():
        pattern = _like(keyword.strip())
        account_query = account_query.where(
            or_(Account.username.ilike(pattern), Account.display_name.ilike(pattern))
        )
        group_query = group_query.where(UserGroup.name.ilike(pattern))
    accounts = db.scalars(account_query.order_by(Account.username).limit(20))
    groups = db.scalars(group_query.order_by(UserGroup.name).limit(20))
    return DirectoryOut(
        accounts=[AccountBrief.model_validate(a) for a in accounts],
        groups=[GroupBrief.model_validate(g) for g in groups],
    )
