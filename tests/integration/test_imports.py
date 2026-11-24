"""Real XLSX parsing and transactional preview/adoption, with isolated PostgreSQL schemas."""

from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from uuid import uuid4
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from control_plane.entrypoints.api import app
from control_plane.infrastructure import db
from control_plane.modules import imports
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration
FIELDS = ["code", "module", "purpose", "steps", "assertions", "preconditions", "level", "status"]
ROW = [
    "OS-001",
    "系统设置",
    "验证设置",
    "1. 打开设置\n2. 返回",
    "设置可见\n返回桌面",
    "设备已开机",
    "P1",
    "启用",
]


def xlsx(records, *, sheet="用例", extras="", cell_override=None):
    """Minimal OOXML protocol fixture; includes string, numeric and formula cells."""

    def column(n):
        result = ""
        while n:
            n, r = divmod(n - 1, 26)
            result = chr(65 + r) + result
        return result

    rows = []
    for i, record in enumerate(records, 1):
        cells = []
        for j, value in enumerate(record, 1):
            ref = f"{column(j)}{i}"
            if value is None:
                continue
            if cell_override and ref in cell_override:
                cells.append(f'<c r="{ref}">{cell_override[ref]}</c>')
            elif isinstance(value, (int, float)):
                cells.append(f'<c r="{ref}" t="n"><v>{value}</v></c>')
            else:
                cells.append(
                    f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{escape(str(value))}</t></is></c>'
                )
        rows.append(f'<row r="{i}">{"".join(cells)}</row>')
    data = BytesIO()
    with ZipFile(data, "w", ZIP_DEFLATED) as z:
        z.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        )
        z.writestr(
            "_rels/.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        )
        z.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="{sheet}" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        )
        z.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1"/><sheetData>'
            + "".join(rows)
            + "</sheetData>"
            + extras
            + "</worksheet>",
        )
    return data.getvalue()


@pytest.fixture
def client(database):
    client = TestClient(app)
    assert (
        client.post("/api/catalogs", json={"kind": "system_module", "name": "系统设置"}).status_code
        == 201
    )
    return client


def make_preview(client, records=None, *, fields=FIELDS, source="回归用例", raw=None, **config):
    raw = raw or xlsx([fields, *(records or [ROW])])
    upload = client.post("/api/imports/excel?filename=cases.xlsx", content=raw)
    assert upload.status_code == 201, upload.text
    body = dict(
        request_id=str(uuid4()),
        upload_id=upload.json()["id"],
        source_key=source,
        sheet="用例",
        mapping={field: i for i, field in enumerate(fields)},
        **config,
    )
    result = client.post("/api/imports/preview", json=body)
    assert result.status_code == 201, result.text
    return result.json(), body, raw


def adopt(client, batch, rows=None, confirmed=None, status=200):
    response = client.post(
        f"/api/imports/{batch['id']}/accept",
        json={"rows": rows or [2], "acknowledged_conflicts": confirmed or []},
    )
    assert response.status_code == status, response.text
    return response.json()


def overview(client):
    return client.get("/api/overview").json()


def test_preview_atomic_adoption_provenance_and_retries(client):
    batch, body, raw = make_preview(client)
    assert batch["preview"][0]["action"] == "new"
    assert overview(client)["cases"] == []
    assert client.post("/api/imports/preview", json=body).json()["id"] == batch["id"]
    assert (
        client.post("/api/imports/preview", json={**body, "source_key": "别的来源"}).status_code
        == 409
    )
    assert client.get(f"/api/imports/uploads/{body['upload_id']}/file").content == raw
    accepted = adopt(client, batch)
    assert adopt(client, batch) == accepted
    data = overview(client)
    assert len(data["cases"]) == len(data["versions"]) == 1
    assert data["cases"][0]["stable_version_id"] is None
    content = data["versions"][0]["content"]
    assert content["steps"] == ["1. 打开设置", "2. 返回"]
    assert content["assertions"] == ["设置可见", "返回桌面"]
    assert content["catalog_snapshot"][content["module_id"]] == "系统设置"
    repeated, _, _ = make_preview(client, [["OS-002", *ROW[1:]], ROW])
    assert [r["action"] for r in repeated["preview"]] == ["new", "unchanged"]
    assert adopt(client, repeated, [3])["result"]["rows"][0]["outcome"] == "unchanged"
    assert len(overview(client)["versions"]) == 1
    adopt(client, repeated, [2], status=409)
    assert len(client.get("/api/imports").json()) == 2


def test_source_changes_platform_conflicts_and_stable_preservation(client):
    batch, _, _ = make_preview(client)
    accepted = adopt(client, batch)["result"]["rows"][0]
    case_id, first = accepted["case_id"], accepted["version_id"]
    assert (
        client.put(
            f"/api/cases/{case_id}/stable", json={"version_id": first, "expected_version_id": None}
        ).status_code
        == 200
    )
    changed = ROW.copy()
    changed[2] = "来源修订"
    next_batch, _, _ = make_preview(client, [changed])
    assert next_batch["preview"][0]["action"] == "update"
    second = adopt(client, next_batch)["result"]["rows"][0]["version_id"]
    content = next_batch["preview"][0]["content"]
    local = client.post(
        f"/api/cases/{case_id}/versions?expected_latest={second}",
        json={
            **content,
            "purpose": "平台修订",
            "steps_markdown": "**1. 打开设置**\n2. 返回",
            "assertions_markdown": "**设置可见**\n返回桌面",
        },
    )
    assert local.status_code == 201
    unchanged, _, _ = make_preview(client, [changed])
    assert unchanged["preview"][0]["action"] == "unchanged"
    assert unchanged["preview"][0]["content"]["purpose"] == "平台修订"
    assert unchanged["preview"][0]["content"]["steps_markdown"] == "**1. 打开设置**\n2. 返回"
    adopt(client, unchanged)
    changed[2] = "来源再次修改"
    changed[3] = "1. 打开快捷设置\n2. 返回"
    conflict, _, _ = make_preview(client, [changed])
    assert conflict["preview"][0]["action"] == "conflict"
    assert "steps_markdown" not in conflict["preview"][0]["content"]
    assert conflict["preview"][0]["content"]["assertions_markdown"] == "**设置可见**\n返回桌面"
    adopt(client, conflict, status=409)
    adopt(client, conflict, confirmed=[2])
    data = overview(client)
    assert len(data["versions"]) == 4
    assert data["cases"][0]["stable_version_id"] == first


def test_existing_code_requires_explicit_link_and_preserves_unmapped_fields(client):
    first, _, _ = make_preview(client)
    adopt(client, first)
    other, _, _ = make_preview(client, fields=FIELDS[:5], records=[ROW[:5]], source="另一份表")
    assert other["preview"][0]["action"] == "link_required"
    assert other["preview"][0]["content"]["preconditions"] == ["设备已开机"]
    adopt(client, other, status=409)
    adopt(client, other, confirmed=[2])
    assert len(overview(client)["versions"]) == 1
    clear, _, _ = make_preview(client, records=[[*ROW[:5], "", *ROW[6:]]])
    assert clear["preview"][0]["content"]["preconditions"] == []


def test_invalid_rows_do_not_block_selected_valid_rows(client):
    records = [
        ROW,
        ["os-001", *ROW[1:]],
        [2, *ROW[1:]],
        ["MISSING", "不存在", *ROW[2:]],
        ["OK", *ROW[1:]],
    ]
    batch, _, _ = make_preview(client, records)
    assert [r["action"] for r in batch["preview"]] == ["invalid"] * 4 + ["new"]
    assert "重复" in batch["preview"][0]["errors"][0]
    assert "文本" in batch["preview"][2]["errors"][0]
    adopt(client, batch, [2, 6], status=409)
    assert overview(client)["cases"] == []
    result = adopt(client, batch, [6])["result"]
    assert result["selected"] == 1 and result["not_selected"] == 4


@pytest.mark.parametrize("change", ["case", "catalog", "source"])
def test_stale_preview_rejects_whole_selection(client, change):
    initial, _, _ = make_preview(client)
    adopted = adopt(client, initial)["result"]["rows"][0]
    modified = ROW.copy()
    modified[2] = "修订"
    pending, _, _ = make_preview(client, [["AA-NEW", *ROW[1:]], modified])
    if change == "case":
        content = pending["preview"][1]["before"]
        assert (
            client.post(
                f"/api/cases/{adopted['case_id']}/versions",
                json={**content, "purpose": "平台抢先修改"},
            ).status_code
            == 201
        )
    elif change == "catalog":
        module = pending["preview"][1]["content"]["module_id"]
        assert (
            client.put(f"/api/catalogs/{module}", json={"name": "系统设置重命名"}).status_code
            == 200
        )
    else:
        competing, _, _ = make_preview(client, [modified])
        adopt(client, competing)
    response = adopt(client, pending, [2, 3], status=409)
    assert response["error"] in {"import_stale", "catalog_changed"}
    assert [c["code"] for c in overview(client)["cases"]] == ["OS-001"]
    assert client.get(f"/api/imports/{pending['id']}").json()["state"] == "preview"


def test_unchanged_source_remains_noop_after_catalog_disabled(client):
    batch, _, _ = make_preview(client)
    adopt(client, batch)
    module = batch["preview"][0]["content"]["module_id"]
    client.put(f"/api/catalogs/{module}", json={"name": "已停用设置", "enabled": False})
    repeat, _, _ = make_preview(client)
    assert repeat["preview"][0]["action"] == "unchanged"
    adopt(client, repeat)
    assert len(overview(client)["versions"]) == 1


def test_app_import_owns_versions_and_rejects_android_fields(client):
    app_id = client.post("/api/catalogs", json={"kind": "application", "name": "相册"}).json()["id"]
    client.post("/api/catalogs", json={"kind": "app_module", "name": "浏览", "parent_id": app_id})
    version = client.post(
        "/api/catalogs", json={"kind": "app_version", "name": "2.3", "parent_id": app_id}
    ).json()["id"]
    client.post("/api/catalogs", json={"kind": "android_version", "name": "Android 16"})
    fields = FIELDS[:5] + ["app", "app_versions"]
    record = ["APP-001", "浏览", *ROW[2:5], "相册", "2.3"]
    good, _, _ = make_preview(client, [record], fields=fields, default_target="app")
    assert good["preview"][0]["content"]["app_version_ids"] == [version]
    adopt(client, good)
    bad, _, _ = make_preview(
        client,
        [[*record, "Android 16"]],
        fields=[*fields, "android_versions"],
        default_target="app",
    )
    assert bad["preview"][0]["action"] == "invalid"
    assert "App 用例不适用" in bad["preview"][0]["errors"][0]


def test_file_validation_formula_locations_and_dimensions(client):
    raw = xlsx([FIELDS, ROW], cell_override={"D2": "<f>1+1</f><v>2</v>"})
    batch, _, _ = make_preview(client, raw=raw)
    assert batch["preview"][0]["row_number"] == 2
    assert "公式" in batch["preview"][0]["errors"][0]
    for raw in [
        b"invalid",
        xlsx([FIELDS, ROW], extras='<mergeCells><mergeCell ref="A1:A2"/></mergeCells>'),
        xlsx([[None] * 51 + ["out of range"]]),
    ]:
        assert client.post("/api/imports/excel?filename=cases.xlsx", content=raw).status_code == 422
    assert client.post("/api/imports/excel?filename=cases.xls", content=b"old").status_code == 422
    assert (
        client.post(
            "/api/imports/excel?filename=cases.xlsx", content=b"x" * (imports.MAX_FILE_BYTES + 1)
        ).status_code
        == 413
    )


def test_concurrent_adoption_is_idempotent(client):
    batch, _, _ = make_preview(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: adopt(TestClient(app), batch), range(2)))
    assert responses[0] == responses[1]
    assert len(overview(client)["versions"]) == 1


def test_stable_and_editor_compare_and_swap(client):
    batch, _, _ = make_preview(client)
    result = adopt(client, batch)["result"]["rows"][0]
    case_id, v1 = result["case_id"], result["version_id"]
    content = batch["preview"][0]["content"]
    stable = f"/api/cases/{case_id}/stable"
    assert client.put(stable, json={"version_id": v1}).status_code == 422
    assert (
        client.put(stable, json={"version_id": v1, "expected_version_id": None}).status_code == 200
    )
    url = f"/api/cases/{case_id}/versions?expected_latest={v1}"
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(
            pool.map(
                lambda n: TestClient(app).post(url, json={**content, "purpose": f"修订{n}"}),
                range(2),
            )
        )
    assert sorted(r.status_code for r in responses) == [201, 409]
    v2 = next(r.json()["id"] for r in responses if r.status_code == 201)
    assert (
        client.put(stable, json={"version_id": v2, "expected_version_id": None}).status_code == 409
    )
    assert client.put(stable, json={"version_id": v2, "expected_version_id": v1}).status_code == 200
    # Historical content can seed an intentional new edit, with the actual latest baseline.
    assert (
        client.post(f"/api/cases/{case_id}/versions?expected_latest={v2}", json=content).status_code
        == 201
    )
    with db.engine.connect() as conn:
        assert db.one(conn, "SELECT count(*) AS n FROM case_versions")["n"] == 3
