"""Managed reference data; immutable versions retain their original labels."""

from uuid import uuid4

from control_plane.infrastructure.db import execute, one, rows
from sqlalchemy.exc import IntegrityError


def conflict(code, detail):
    from control_plane.modules.service import Conflict

    raise Conflict(code, detail)


def list_entries(conn):
    return rows(
        conn,
        """SELECT e.*, (SELECT count(*) FROM case_version_catalog_refs r
        WHERE r.entry_id=e.id) AS reference_count FROM catalog_entries e
        ORDER BY e.kind,lower(e.name),e.id""",
    )


def save_entry(conn, values, subject, entry_id=None):
    from control_plane.modules.service import audit

    if entry_id:
        old = one(conn, "SELECT * FROM catalog_entries WHERE id=:id FOR UPDATE", id=entry_id)
        if not old:
            conflict("not_found", "基础数据不存在")
        parent_id = old["parent_id"]
    else:
        parent_id = values.get("parent_id")
    if parent_id:
        parent = one(conn, "SELECT * FROM catalog_entries WHERE id=:id FOR SHARE", id=parent_id)
        if not parent or parent["kind"] != "application":
            conflict("invalid_app", "App 模块与版本必须归属有效的 App")
        if values["enabled"] and not parent["enabled"]:
            conflict("app_disabled", "请先启用所属 App")
    try:
        with conn.begin_nested():
            if entry_id:
                result = one(
                    conn,
                    """UPDATE catalog_entries SET name=:name,description=:description,
                    enabled=:enabled,updated_at=now() WHERE id=:id RETURNING *""",
                    id=entry_id,
                    **values,
                )
            else:
                result = one(
                    conn,
                    """INSERT INTO catalog_entries(id,kind,name,description,enabled,parent_id)
                    VALUES (:id,:kind,:name,:description,:enabled,:parent_id) RETURNING *""",
                    id=uuid4(),
                    **values,
                )
    except IntegrityError:
        conflict("duplicate_catalog_name", "同一分类和 App 下已存在该名称")
    audit(conn, subject, "catalog.update" if entry_id else "catalog.create", result["id"])
    return dict(result)


def delete_entry(conn, entry_id, subject):
    from control_plane.modules.service import audit

    if not one(conn, "SELECT id FROM catalog_entries WHERE id=:id FOR UPDATE", id=entry_id):
        conflict("not_found", "基础数据不存在")
    if one(
        conn,
        "SELECT version_id FROM case_version_catalog_refs WHERE entry_id=:id LIMIT 1",
        id=entry_id,
    ) or one(conn, "SELECT id FROM catalog_entries WHERE parent_id=:id LIMIT 1", id=entry_id):
        conflict("catalog_in_use", "已被用例版本或下级数据引用，不能删除；可改为停用")
    execute(conn, "DELETE FROM catalog_entries WHERE id=:id", id=entry_id)
    audit(conn, subject, "catalog.delete", entry_id)
    return {"deleted": True}


def resolve_content(conn, content, case_code):
    from platform_contracts.models import CaseContent

    value = CaseContent.model_validate(content).model_dump(mode="json")
    fields = {
        "module_id": "system_module" if value["target"] == "android_os" else "app_module",
        "app_id": "application",
        "android_version_ids": "android_version",
        "app_version_ids": "app_version",
        "device_model_ids": "device_model",
        "chip_platform_ids": "chip_platform",
    }
    refs = {}
    for field, kind in fields.items():
        ids = value[field] if isinstance(value[field], list) else [value[field]]
        for entry_id in ids:
            if not entry_id:
                continue
            entry = one(conn, "SELECT * FROM catalog_entries WHERE id=:id FOR SHARE", id=entry_id)
            if not entry or entry["kind"] != kind:
                conflict("invalid_catalog_reference", f"{field} 引用了不存在或分类错误的基础数据")
            if not entry["enabled"]:
                conflict("catalog_disabled", f"{entry['name']} 已停用，请重新选择")
            if kind in {"app_module", "app_version"} and str(entry["parent_id"]) != value["app_id"]:
                conflict("app_scope_mismatch", "模块和 App 版本必须属于所选 App")
            refs[entry_id] = entry["name"]
    value["catalog_snapshot"] = refs
    value["case_code"] = case_code
    return value
