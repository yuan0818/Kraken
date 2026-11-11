"""Server-side search over the latest authored version, independently of Stable."""

from control_plane.infrastructure.db import one, rows


def search(
    conn,
    *,
    page=1,
    page_size=20,
    q=None,
    code=None,
    purpose=None,
    target=None,
    module_id=None,
    app_id=None,
    level=None,
    status=None,
    android_version_id=None,
    app_version_id=None,
):
    clauses, params = ["true"], {}
    for field, sql in {
        "q": "concat_ws(' ',c.code,c.name,v.content->>'purpose',v.content->>'title')",
        "code": "c.code",
        "purpose": "coalesce(nullif(v.content->>'purpose',''),v.content->>'title')",
    }.items():
        value = {"q": q, "code": code, "purpose": purpose}[field]
        if value:
            clauses.append(f"{sql} ILIKE :{field} ESCAPE '\\'")
            params[field] = (
                "%" + value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            )
    for field, value in {
        "target": target,
        "module_id": module_id,
        "app_id": app_id,
        "level": level,
        "status": status,
    }.items():
        if value:
            expression = f"v.content->>'{field}'"
            if field in {"level", "status"}:
                expression = f"coalesce({expression},'{'P2' if field == 'level' else 'active'}')"
            clauses.append(f"{expression} = :{field}")
            params[field] = str(value)
    for field, value in {
        "android_version_ids": android_version_id,
        "app_version_ids": app_version_id,
    }.items():
        if value:
            clauses.append(f"coalesce(v.content->'{field}','[]'::jsonb) ? :{field}")
            params[field] = str(value)
    source = """FROM cases c LEFT JOIN LATERAL (SELECT * FROM case_versions cv
        WHERE cv.case_id=c.id ORDER BY cv.created_at DESC,cv.id DESC LIMIT 1) v ON true
        LEFT JOIN case_versions sv ON sv.id=c.stable_version_id
        WHERE """ + " AND ".join(clauses)
    total = one(conn, "SELECT count(*) AS total " + source, **params)["total"]
    items = rows(
        conn,
        """SELECT c.*,v.id AS latest_version_id,v.content AS latest_content,
        v.created_at AS updated_at,
        (sv.id IS NOT NULL AND coalesce(sv.content->>'status','active')='active') AS stable_ready """
        + source
        + " ORDER BY coalesce(v.created_at,c.created_at) DESC,c.id DESC LIMIT :limit OFFSET :offset",
        **params,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    return {"items": items, "total": total, "page": page, "page_size": page_size}
