"""Preview-first case imports. Adoption is atomic and never moves Stable."""

import hashlib
import io
import re
from collections import Counter
from uuid import uuid4
from zipfile import ZipFile

from control_plane.infrastructure.db import execute, one, rows
from control_plane.modules import catalogs, service
from defusedxml.ElementTree import fromstring
from openpyxl import load_workbook
from openpyxl.utils.cell import column_index_from_string, coordinate_from_string
from platform_contracts.imports import ImportRow
from platform_contracts.models import CaseContent, CreateCase
from pydantic import ValidationError

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_ROWS = 2000
MAX_COLUMNS = 50
REQUIRED = {"code", "module", "purpose", "steps", "assertions"}
SET_FIELDS = {
    "required_tags",
    "android_version_ids",
    "app_version_ids",
    "device_model_ids",
    "chip_platform_ids",
}


def digest(value):
    return hashlib.sha256(service.encode(value).encode()).hexdigest()


def parse_workbook(raw):
    if not raw or len(raw) > MAX_FILE_BYTES:
        raise ValueError("文件不能为空，且不能超过 10 MB")
    try:
        with ZipFile(io.BytesIO(raw)) as archive:
            entries = archive.infolist()
            if len(entries) > 1000 or sum(e.file_size for e in entries) > 40 * 1024 * 1024:
                raise ValueError("文件展开后过大，请拆分后导入")
            for item in entries:
                if (
                    item.filename.startswith(("xl/media/", "xl/embeddings/"))
                    or "vbaProject" in item.filename
                ):
                    raise ValueError(
                        "文件含图片、嵌入附件或宏，请先整理为完整文本用例，避免遗漏检查内容"
                    )
                if item.filename.startswith("xl/worksheets/") and item.filename.endswith(".xml"):
                    root = fromstring(archive.read(item))
                    for cell in root.iter():
                        if cell.tag.endswith("}c") and cell.attrib.get("r"):
                            col, row = coordinate_from_string(cell.attrib["r"])
                            if column_index_from_string(col) > MAX_COLUMNS:
                                raise ValueError("最多支持 50 列，请移除无关列后导入")
                            if row > MAX_ROWS + 20:
                                raise ValueError("单表最多支持 2000 条用例和 20 行表头，请拆分文件")
                    if any(e.tag.endswith("}mergeCell") for e in root.iter()):
                        raise ValueError("文件含合并单元格，请取消合并并为每行补齐内容后导入")
        workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=False, keep_links=False)
        try:
            if len(workbook.worksheets) > 20:
                raise ValueError("最多支持 20 个工作表，请拆分文件")
            result = []
            total = 0
            for sheet in workbook.worksheets:
                sheet.reset_dimensions()
                records, issues = [], {}
                for number, cells in enumerate(sheet.iter_rows(max_col=MAX_COLUMNS + 1), 1):
                    if number > MAX_ROWS + 20:
                        raise ValueError("单表最多支持 2000 条用例和 20 行表头，请拆分文件")
                    if cells[MAX_COLUMNS].value is not None:
                        raise ValueError("最多支持 50 列，请移除无关列后导入")
                    values = []
                    for column, cell in enumerate(cells[:MAX_COLUMNS]):
                        value = cell.value
                        if value is not None and not isinstance(value, (str, int, float, bool)):
                            value = str(value)
                        if isinstance(value, str) and len(value) > 20000:
                            raise ValueError(f"{sheet.title} 第 {number} 行单元格文本过长")
                        if cell.data_type in {"f", "e"}:
                            issues[f"{number}:{column}"] = (
                                "公式或 Excel 错误值，请转换成经过核对的文本"
                            )
                        values.append(value)
                    while values and values[-1] is None:
                        values.pop()
                    records.append(values)
                    if values:
                        total += 1
                    if total > MAX_ROWS + 20 * len(workbook.worksheets):
                        raise ValueError("文件合计最多支持 2000 条用例，请拆分文件")
                while records and not records[-1]:
                    records.pop()
                result.append({"name": sheet.title, "rows": records, "issues": issues})
            if not any(s["rows"] for s in result):
                raise ValueError("文件中没有可导入的数据")
            return result
        finally:
            workbook.close()
    except ValueError:
        raise
    except Exception as exc:
        # Parser errors contain paths/XML details; keep the response actionable and bounded.
        raise ValueError("无法读取此 Excel 文件，请使用未加密的 .xlsx 文件") from exc


def upload(conn, filename, raw, workbook, subject):
    upload_id = uuid4()
    execute(
        conn,
        """INSERT INTO import_uploads(id,filename,sha256,original,workbook,created_by)
        VALUES (:id,:filename,:sha,:raw,CAST(:workbook AS jsonb),:subject)""",
        id=upload_id,
        filename=filename,
        sha=hashlib.sha256(raw).hexdigest(),
        raw=raw,
        workbook=service.encode(workbook),
        subject=subject,
    )
    service.audit(conn, subject, "import.upload", upload_id)
    return get_upload(conn, upload_id)


def get_upload(conn, upload_id):
    record = one(
        conn, "SELECT id,filename,sha256,workbook FROM import_uploads WHERE id=:id", id=upload_id
    )
    if not record:
        raise service.Conflict("not_found", "上传文件不存在")
    return {
        "id": record["id"],
        "filename": record["filename"],
        "sha256": record["sha256"],
        "sheets": [
            {"name": s["name"], "row_count": len(s["rows"]), "sample_rows": s["rows"][:20]}
            for s in record["workbook"]
        ],
        "warnings": ["公式与错误值不可作为用例内容；未映射的列保留在原文件中，不会用于执行。"],
    }


def latest(conn, case_id):
    return one(
        conn,
        "SELECT * FROM case_versions WHERE case_id=:id ORDER BY created_at DESC,id DESC LIMIT 1",
        id=case_id,
    )


def semantic(content):
    value = CaseContent.model_validate(content).model_dump(mode="json")
    for key in ("schema_version", "case_code", "catalog_snapshot"):
        value.pop(key)
    for key in SET_FIELDS:
        value[key] = sorted(set(value[key]))
    return value


def changed_fields(before, after):
    old = semantic(before) if before else {}
    new = semantic(after)
    return [key for key in new if old.get(key) != new[key]]


def text(value):
    return "" if value is None else str(value).strip()


def lines(value):
    return [v.strip() for v in text(value).splitlines() if v.strip()]


def selections(value):
    return list(dict.fromkeys(v.strip() for v in re.split(r"[\n;；]", text(value)) if v.strip()))


def resolve_name(conn, kind, name, parent=None):
    entry = one(
        conn,
        """SELECT * FROM catalog_entries WHERE kind=:kind AND lower(name)=lower(:name)
        AND parent_id IS NOT DISTINCT FROM CAST(:parent AS uuid)""",
        kind=kind,
        name=name,
        parent=parent,
    )
    if not entry or not entry["enabled"]:
        raise ValueError(f"基础数据“{name}”不存在或已停用，请先维护对应分类和 App 归属")
    return str(entry["id"])


def normalize(conn, values, existing, previous):
    target = text(values["target"])
    target = {
        "Android OS": "android_os",
        "Android": "android_os",
        "OS": "android_os",
        "App": "app",
        "APP": "app",
    }.get(target, target)
    base = dict(previous or {})
    if base.get("target") != target:
        for key in ("app_id", "module_id"):
            base[key] = None
        for key in ("app_version_ids", "android_version_ids", "chip_platform_ids"):
            base[key] = []
    base.update(schema_version=2, target=target)
    base.setdefault("title", "Excel 导入")
    base.setdefault("required_tags", [])
    for field in ("purpose", "title", "level", "status"):
        if field in values:
            base[field] = text(values[field])
    if "status" in base:
        base["status"] = {"草稿": "draft", "启用": "active", "停用": "disabled"}.get(
            base["status"], base["status"]
        )
    for field in ("preconditions", "steps", "assertions"):
        if field in values:
            items = lines(values[field])
            if field in {"steps", "assertions"} and items != base.get(field):
                base.pop(f"{field}_markdown", None)
            base[field] = items
    if "required_tags" in values:
        base["required_tags"] = selections(values["required_tags"])
    if "app" in values:
        base["app_id"] = (
            resolve_name(conn, "application", text(values["app"])) if text(values["app"]) else None
        )
    if "module" in values:
        base["module_id"] = resolve_name(
            conn,
            "app_module" if target == "app" else "system_module",
            text(values["module"]),
            base.get("app_id") if target == "app" else None,
        )
    for source, field, kind in (
        ("android_versions", "android_version_ids", "android_version"),
        ("app_versions", "app_version_ids", "app_version"),
        ("device_models", "device_model_ids", "device_model"),
        ("chip_platforms", "chip_platform_ids", "chip_platform"),
    ):
        if source in values:
            base[field] = [
                resolve_name(
                    conn, kind, name, base.get("app_id") if kind == "app_version" else None
                )
                for name in selections(values[source])
            ]
    name = text(values.get("name")) or (
        existing["name"] if existing else text(values.get("purpose"))[:200]
    )
    if existing and name != existing["name"]:
        raise ValueError("编号已存在但用例名称不同，请核对来源编号和名称，不能按标题猜测合并")
    CreateCase(name=name, code=text(values.get("code")))
    content = catalogs.resolve_content(
        conn, base, existing["code"] if existing else text(values.get("code"))
    )
    return name, content


def source_link(conn, key, sheet, code):
    return one(
        conn,
        """SELECT * FROM case_source_links
        WHERE source='excel' AND source_key=:key AND sheet=:sheet AND source_code=:code""",
        key=key,
        sheet=sheet,
        code=code.lower(),
    )


def preview(conn, body, subject):
    config = body.model_dump(mode="json")
    fingerprint = digest(config)
    execute(
        conn, "SELECT pg_advisory_xact_lock(hashtextextended(:key,0))", key=str(body.request_id)
    )
    existing_batch = one(
        conn, "SELECT id,fingerprint FROM import_batches WHERE request_id=:id", id=body.request_id
    )
    if existing_batch:
        if existing_batch["fingerprint"] != fingerprint:
            raise service.Conflict("request_id_conflict", "同一预览请求不能更换文件或映射")
        return get_batch(conn, existing_batch["id"])
    uploaded = one(conn, "SELECT * FROM import_uploads WHERE id=:id", id=body.upload_id)
    if not uploaded:
        raise service.Conflict("not_found", "上传文件不存在")
    sheet = next((s for s in uploaded["workbook"] if s["name"] == body.sheet), None)
    if not sheet or body.header_row >= len(sheet["rows"]):
        raise service.Conflict("invalid_sheet", "所选工作表或表头后没有数据")
    data = sheet["rows"][body.header_row :]
    if len(data) > MAX_ROWS:
        raise service.Conflict("too_many_rows", "单批最多导入 2000 行")

    def get_cell(row, col):
        return row[col] if col < len(row) else None

    counts = Counter(text(get_cell(row, body.mapping["code"])).lower() for row in data if row)
    output = []
    for number, row in enumerate(data, body.header_row + 1):
        if not any(value is not None and text(value) for value in row):
            continue
        values = {field: get_cell(row, col) for field, col in body.mapping.items()}
        values.setdefault("target", body.default_target)
        code = text(values.get("code"))
        record = ImportRow(
            row_number=number,
            code=code,
            name=text(values.get("name")),
            action="invalid",
            raw=values,
        )
        try:
            errors = [
                sheet["issues"][f"{number}:{col}"]
                for col in body.mapping.values()
                if f"{number}:{col}" in sheet["issues"]
            ]
            if errors:
                raise ValueError("；".join(dict.fromkeys(errors)))
            if not isinstance(values.get("code"), str) or not code:
                raise ValueError("编号必须是非空文本，请将 Excel 编号列设置为文本以保留前导零")
            if counts[code.lower()] > 1:
                raise ValueError("来源编号重复，同一表内编号必须唯一（不区分大小写）")
            existing = one(conn, "SELECT * FROM cases WHERE lower(code)=lower(:code)", code=code)
            old = latest(conn, existing["id"]) if existing else None
            link = source_link(conn, body.source_key, body.sheet, code)
            record.case_id = existing["id"] if existing else None
            record.expected_latest_version_id = old["id"] if old else None
            record.before = CaseContent.model_validate(old["content"]) if old else None
            record.previous_source_digest = link["source_digest"] if link else None
            record.source_digest = digest(
                {field: text(value).replace("\r\n", "\n") for field, value in values.items()}
            )
            if link and link["source_digest"] == record.source_digest and old:
                record.action = "unchanged"
                record.name = existing["name"]
                record.content = record.before
                record.notes.append("来源内容未变，保留当前平台版本")
                output.append(record.model_dump(mode="json"))
                continue
            name, content = normalize(conn, values, existing, old["content"] if old else None)
            record.name, record.content = name, CaseContent.model_validate(content)
            record.changed_fields = changed_fields(old["content"] if old else None, content)
            if link and link["source_digest"] == record.source_digest:
                record.action = "unchanged"
                record.notes.append("来源内容未变，保留当前平台版本")
            elif not existing:
                record.action = "new"
            elif not link:
                record.action = "link_required"
                record.notes.append("该编号已存在，需确认将此来源关联到已有用例")
            elif not record.changed_fields:
                record.action = "unchanged"
                record.notes.append("内容与平台一致，不新增版本")
            elif old and str(old["id"]) != str(link["version_id"]):
                record.action = "conflict"
                record.notes.append("平台和来源均有修改，请核对差异后明确确认采纳来源内容")
            else:
                record.action = "update"
        except (ValueError, service.Conflict) as exc:
            record.errors = [
                "；".join(e["msg"] for e in exc.errors())
                if isinstance(exc, ValidationError)
                else str(exc)
            ]
        output.append(record.model_dump(mode="json"))
    batch_id = uuid4()
    identity = {
        "source_key": body.source_key,
        "sheet": body.sheet,
        "filename": uploaded["filename"],
        "sha256": uploaded["sha256"],
    }
    execute(
        conn,
        """INSERT INTO import_batches(id,source,state,source_identity,upload_id,request_id,
        fingerprint,configuration,preview,created_by) VALUES (:id,'excel','preview',CAST(:identity AS jsonb),
        :upload,:request,:fingerprint,CAST(:config AS jsonb),CAST(:preview AS jsonb),:subject)""",
        id=batch_id,
        identity=service.encode(identity),
        upload=body.upload_id,
        request=body.request_id,
        fingerprint=fingerprint,
        config=service.encode(config),
        preview=service.encode(output),
        subject=subject,
    )
    service.audit(conn, subject, "import.preview", batch_id)
    return get_batch(conn, batch_id)


def get_batch(conn, batch_id):
    record = one(
        conn,
        """SELECT id,source,state,source_identity,configuration,preview,result,created_at
        FROM import_batches WHERE id=:id""",
        id=batch_id,
    )
    if not record:
        raise service.Conflict("not_found", "导入批次不存在")
    return dict(record)


def list_batches(conn):
    return rows(
        conn,
        """SELECT id,source,state,source_identity,result,created_at
        FROM import_batches ORDER BY created_at DESC LIMIT 100""",
    )


def accept(conn, batch_id, body, subject):
    batch = one(conn, "SELECT * FROM import_batches WHERE id=:id FOR UPDATE", id=batch_id)
    if not batch:
        raise service.Conflict("not_found", "导入批次不存在")
    acceptance = body.model_dump(mode="json")
    if batch["acceptance"] is not None:
        if batch["acceptance"] != acceptance:
            raise service.Conflict("batch_already_accepted", "该批次已采纳，请新建预览以处理其余行")
        return get_batch(conn, batch_id)
    identity = batch["source_identity"]
    key, sheet = identity["source_key"], identity["sheet"]
    execute(
        conn,
        "SELECT pg_advisory_xact_lock(hashtextextended(:key,0))",
        key=f"import:excel:{key}:{sheet}",
    )
    selected = [row for row in batch["preview"] if row["row_number"] in body.rows]
    if len(selected) != len(body.rows) or any(row["action"] == "invalid" for row in selected):
        raise service.Conflict("invalid_selection", "只能选择预览中校验通过的行")
    if any(
        row["action"] in {"link_required", "conflict"}
        and row["row_number"] not in body.acknowledged_conflicts
        for row in selected
    ):
        raise service.Conflict("confirmation_required", "请逐行确认关联或冲突后再采纳")
    results = []
    for row in sorted(selected, key=lambda r: r["code"].lower()):
        # A per-code lock also serializes unrelated sources creating the same case.
        execute(
            conn,
            "SELECT pg_advisory_xact_lock(hashtextextended(:key,0))",
            key=f"import:case:{row['code'].lower()}",
        )
        case = one(
            conn, "SELECT * FROM cases WHERE lower(code)=lower(:code) FOR UPDATE", code=row["code"]
        )
        old = latest(conn, case["id"]) if case else None
        actual = str(old["id"]) if old else None
        link = source_link(conn, key, sheet, row["code"])
        if (
            actual != row["expected_latest_version_id"]
            or (str(case["id"]) if case else None) != row["case_id"]
            or (link["source_digest"] if link else None) != row["previous_source_digest"]
        ):
            raise service.Conflict(
                "import_stale",
                f"第 {row['row_number']} 行在预览后已有变更，请重新预览；本次未采纳任何行",
            )
        unchanged_source = link and link["source_digest"] == row["source_digest"]
        content = (
            row["content"]
            if unchanged_source
            else catalogs.resolve_content(
                conn, row["content"], case["code"] if case else row["code"]
            )
        )
        if content != row["content"]:
            raise service.Conflict("catalog_changed", "基础数据在预览后已改变，请重新预览")
        if not case:
            case = service.create_case(conn, row["name"], subject, row["code"])
        if old and (unchanged_source or not changed_fields(old["content"], content)):
            version = old
            outcome = "unchanged"
        else:
            version = service.add_version(conn, case["id"], content, subject)
            outcome = "created" if not old else "updated"
        if not unchanged_source:
            execute(
                conn,
                """INSERT INTO case_source_links(source,source_key,sheet,source_code,case_id,version_id,source_digest,batch_id)
                VALUES ('excel',:key,:sheet,:code,:case,:version,:digest,:batch)
                ON CONFLICT(source,source_key,sheet,source_code) DO UPDATE SET
                version_id=EXCLUDED.version_id,source_digest=EXCLUDED.source_digest,batch_id=EXCLUDED.batch_id""",
                key=key,
                sheet=sheet,
                code=row["code"].lower(),
                case=case["id"],
                version=version["id"],
                digest=row["source_digest"],
                batch=batch_id,
            )
        results.append(
            {
                "row_number": row["row_number"],
                "code": row["code"],
                "case_id": str(case["id"]),
                "version_id": str(version["id"]),
                "outcome": outcome,
            }
        )
    result = {
        "rows": results,
        "selected": len(results),
        "not_selected": len(batch["preview"]) - len(results),
    }
    execute(
        conn,
        """UPDATE import_batches SET state='accepted',acceptance=CAST(:acceptance AS jsonb),
        result=CAST(:result AS jsonb) WHERE id=:id""",
        id=batch_id,
        acceptance=service.encode(acceptance),
        result=service.encode(result),
    )
    service.audit(conn, subject, "import.accept", batch_id)
    return get_batch(conn, batch_id)
