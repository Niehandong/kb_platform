"""分组管理（平台管理员）：分组的增删改查与成员增减。"""

from fastapi import APIRouter
from sqlalchemy import delete, func, select

from app.audit.service import log_operation
from app.auth.deps import AdminAccount, Db
from app.auth.models import SUBJECT_GROUP, Account, GroupMember, KbGrant, UserGroup
from app.auth.schemas import AccountBrief, GroupIn, GroupOut, GroupUpdateIn, MembersIn
from app.common.errors import AppError

router = APIRouter(tags=["分组管理"])


def _get_group(db: Db, group_id: str) -> UserGroup:
    group = db.get(UserGroup, group_id)
    if group is None:
        raise AppError(404, "NOT_FOUND", "分组不存在")
    return group


def _name_taken(db: Db, name: str, exclude_id: str | None = None) -> bool:
    query = select(UserGroup.id).where(UserGroup.name == name)
    if exclude_id:
        query = query.where(UserGroup.id != exclude_id)
    return db.scalar(query) is not None


def _out(group: UserGroup, member_count: int) -> GroupOut:
    return GroupOut(
        id=group.id,
        name=group.name,
        description=group.description,
        member_count=member_count,
        created_at=group.created_at,
    )


def _member_count(db: Db, group_id: str) -> int:
    return (
        db.scalar(select(func.count()).select_from(GroupMember).where(GroupMember.group_id == group_id)) or 0
    )


@router.get("/groups", response_model=list[GroupOut], summary="分组列表")
def list_groups(_: AdminAccount, db: Db) -> list[GroupOut]:
    counts = dict(db.execute(select(GroupMember.group_id, func.count()).group_by(GroupMember.group_id)).all())
    return [_out(g, counts.get(g.id, 0)) for g in db.scalars(select(UserGroup).order_by(UserGroup.name))]


@router.post("/groups", response_model=GroupOut, summary="创建分组")
def create_group(body: GroupIn, admin: AdminAccount, db: Db) -> GroupOut:
    name = body.name.strip()
    if _name_taken(db, name):
        raise AppError(409, "GROUP_EXISTS", "分组名称已存在")
    group = UserGroup(name=name, description=body.description.strip())
    db.add(group)
    db.flush()
    log_operation(db, admin.id, "group.create", "group", group.id, detail={"name": name})
    db.commit()
    return _out(group, 0)


@router.patch("/groups/{group_id}", response_model=GroupOut, summary="修改分组名称或说明")
def update_group(group_id: str, body: GroupUpdateIn, admin: AdminAccount, db: Db) -> GroupOut:
    group = _get_group(db, group_id)
    before = {"name": group.name, "description": group.description}
    if body.name is not None:
        name = body.name.strip()
        if _name_taken(db, name, exclude_id=group.id):
            raise AppError(409, "GROUP_EXISTS", "分组名称已存在")
        group.name = name
    if body.description is not None:
        group.description = body.description.strip()
    after = {"name": group.name, "description": group.description}
    if after != before:
        log_operation(
            db, admin.id, "group.update", "group", group.id, detail={"before": before, "after": after}
        )
        db.commit()
    return _out(group, _member_count(db, group.id))


@router.delete("/groups/{group_id}", summary="删除分组，同时移除其成员关系和它获得的所有知识库授权")
def delete_group(group_id: str, admin: AdminAccount, db: Db) -> dict:
    group = _get_group(db, group_id)
    members = _member_count(db, group.id)
    grants = db.execute(
        delete(KbGrant).where(KbGrant.subject_type == SUBJECT_GROUP, KbGrant.subject_id == group.id)
    ).rowcount
    db.execute(delete(GroupMember).where(GroupMember.group_id == group.id))
    db.delete(group)
    log_operation(
        db,
        admin.id,
        "group.delete",
        "group",
        group.id,
        detail={"name": group.name, "removed_members": members, "removed_grants": grants},
    )
    db.commit()
    return {"removed_members": members, "removed_grants": grants}


@router.get("/groups/{group_id}/members", response_model=list[AccountBrief], summary="分组成员列表")
def list_members(group_id: str, _: AdminAccount, db: Db) -> list:
    _get_group(db, group_id)
    query = (
        select(Account)
        .join(GroupMember, GroupMember.account_id == Account.id)
        .where(GroupMember.group_id == group_id)
        .order_by(Account.username)
    )
    return list(db.scalars(query))


@router.post("/groups/{group_id}/members", summary="向分组添加成员，已在组内的自动跳过")
def add_members(group_id: str, body: MembersIn, admin: AdminAccount, db: Db) -> dict:
    group = _get_group(db, group_id)
    wanted = list(dict.fromkeys(body.account_ids))
    found = set(db.scalars(select(Account.id).where(Account.id.in_(wanted))))
    missing = [a for a in wanted if a not in found]
    if missing:
        raise AppError(400, "INVALID_PARAM", "有账号不存在", {"account_ids": missing})
    already = set(
        db.scalars(
            select(GroupMember.account_id).where(
                GroupMember.group_id == group.id, GroupMember.account_id.in_(wanted)
            )
        )
    )
    added = [a for a in wanted if a not in already]
    db.add_all(GroupMember(group_id=group.id, account_id=a) for a in added)
    if added:
        log_operation(
            db,
            admin.id,
            "group.member_add",
            "group",
            group.id,
            detail={"name": group.name, "account_ids": added},
        )
        db.commit()
    return {"added": len(added), "skipped": len(already)}


@router.delete("/groups/{group_id}/members/{account_id}", summary="从分组移除成员")
def remove_member(group_id: str, account_id: str, admin: AdminAccount, db: Db) -> dict:
    group = _get_group(db, group_id)
    removed = db.execute(
        delete(GroupMember).where(GroupMember.group_id == group.id, GroupMember.account_id == account_id)
    ).rowcount
    if not removed:
        raise AppError(404, "NOT_FOUND", "该账号不在分组内")
    log_operation(
        db,
        admin.id,
        "group.member_remove",
        "group",
        group.id,
        detail={"name": group.name, "account_id": account_id},
    )
    db.commit()
    return {"removed": removed}
