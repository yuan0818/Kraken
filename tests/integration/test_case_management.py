from uuid import uuid4

import pytest
from control_plane.entrypoints.api import app
from control_plane.infrastructure import db
from control_plane.modules import service
from fastapi.testclient import TestClient
from platform_contracts.models import Command, NodeEvent, SubmitRun

pytestmark = pytest.mark.integration


def entry(client, kind, name, parent_id=None):
    response = client.post(
        "/api/catalogs", json={"kind": kind, "name": name, "parent_id": parent_id}
    )
    assert response.status_code == 201, response.text
    return response.json()


def os_content(client, **overrides):
    module = entry(client, "system_module", "系统设置")
    android = entry(client, "android_version", "Android 16")
    return dict(
        schema_version=2,
        title="设置页面",
        target="android_os",
        purpose="验证设置页面可正常打开",
        preconditions=["设备已完成开机向导"],
        steps=["打开设置"],
        assertions=["显示设置页面"],
        required_tags=["simulator"],
        module_id=module["id"],
        android_version_ids=[android["id"]],
        level="P1",
        status="active",
        **overrides,
    )


def create(client, code, content):
    case = client.post("/api/cases", json={"name": "设置用例", "code": code})
    assert case.status_code == 201, case.text
    version = client.post(f"/api/cases/{case.json()['id']}/versions", json=content)
    assert version.status_code == 201, version.text
    return case.json(), version.json()


def test_code_uniqueness_and_legacy_compatibility(database):
    client = TestClient(app)
    first = client.post("/api/cases", json={"name": "旧格式"})
    assert first.status_code == 201 and first.json()["code"].startswith("TC-")
    second = client.post(
        "/api/cases", json={"name": "另一个用例", "code": first.json()["code"].lower()}
    )
    assert second.status_code == 409
    version = client.post(
        f"/api/cases/{first.json()['id']}/versions",
        json={
            "title": "历史格式",
            "target": "android_os",
            "steps": ["操作"],
            "assertions": ["结果"],
        },
    )
    assert version.status_code == 201
    assert version.json()["content"]["case_code"] == first.json()["code"]
    assert client.get("/api/overview").status_code == 200


def test_reference_rename_disable_delete_and_immutable_labels(database):
    client = TestClient(app)
    content = os_content(client)
    case, version = create(client, "OS-001", content)
    module_id = content["module_id"]
    assert version["content"]["catalog_snapshot"][module_id] == "系统设置"
    assert (
        client.put(f"/api/catalogs/{module_id}", json={"name": "设置与个性化"}).status_code == 200
    )
    record = next(
        v for v in client.get("/api/overview").json()["versions"] if v["id"] == version["id"]
    )
    assert record["content"]["catalog_snapshot"][module_id] == "系统设置"
    assert client.delete(f"/api/catalogs/{module_id}").status_code == 409
    assert (
        client.put(
            f"/api/catalogs/{module_id}", json={"name": "设置与个性化", "enabled": False}
        ).status_code
        == 200
    )
    assert client.post(f"/api/cases/{case['id']}/versions", json=content).status_code == 409
    unused = entry(client, "device_model", "临时机型")
    assert client.delete(f"/api/catalogs/{unused['id']}").status_code == 200


def test_app_module_and_version_ownership_and_conditional_fields(database):
    client = TestClient(app)
    app_a = entry(client, "application", "相册")
    app_b = entry(client, "application", "相机")
    module = entry(client, "app_module", "浏览", app_a["id"])
    version = entry(client, "app_version", "2.0", app_a["id"])
    wrong = entry(client, "app_version", "2.0", app_b["id"])
    content = dict(
        schema_version=2,
        title="浏览照片",
        target="app",
        purpose="验证照片展示",
        steps=["打开照片"],
        assertions=["显示原图"],
        app_id=app_a["id"],
        module_id=module["id"],
        app_version_ids=[version["id"]],
        device_model_ids=[],
    )
    case, _ = create(client, "APP-001", content)
    url = f"/api/cases/{case['id']}/versions"
    assert client.post(url, json={**content, "app_version_ids": [wrong["id"]]}).status_code == 409
    assert client.post(url, json={**content, "app_id": app_b["id"]}).status_code == 409
    for field in ["android_version_ids", "chip_platform_ids"]:
        assert client.post(url, json={**content, field: [str(uuid4())]}).status_code == 422
    assert (
        client.get("/api/cases", params={"target": "app", "app_version_id": version["id"]}).json()[
            "total"
        ]
        == 1
    )
    assert client.delete(f"/api/catalogs/{app_b['id']}").status_code == 409


def test_catalog_duplicate_and_invalid_parent(database):
    client = TestClient(app)
    entry(client, "android_version", "Android 16")
    assert (
        client.post(
            "/api/catalogs", json={"kind": "android_version", "name": "android 16"}
        ).status_code
        == 409
    )
    assert (
        client.post("/api/catalogs", json={"kind": "app_module", "name": "设置"}).status_code == 422
    )
    assert (
        client.post(
            "/api/catalogs", json={"kind": "app_version", "name": "2", "parent_id": str(uuid4())}
        ).status_code
        == 409
    )


def test_search_combines_fields_paginates_and_uses_latest_not_stable(database):
    client = TestClient(app)
    content = os_content(client)
    case, v1 = create(client, "OS-001", content)
    client.put(
        f"/api/cases/{case['id']}/stable",
        json={"version_id": v1["id"], "expected_version_id": None},
    )
    second = client.post(
        f"/api/cases/{case['id']}/versions",
        json={**content, "purpose": "验证中文搜索", "level": "P0", "status": "draft"},
    )
    assert second.status_code == 201
    create(client, "OS-002", content)
    response = client.get(
        "/api/cases",
        params={
            "code": "os-001",
            "purpose": "中文",
            "module_id": content["module_id"],
            "level": "P0",
            "status": "draft",
            "android_version_id": content["android_version_ids"][0],
            "target": "android_os",
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["total"] == 1
    assert result["items"][0]["stable_version_id"] == v1["id"]
    assert result["items"][0]["latest_version_id"] == second.json()["id"]
    assert client.get("/api/cases", params={"purpose": "%"}).json()["total"] == 0
    first = client.get("/api/cases", params={"page_size": 1}).json()
    last = client.get("/api/cases", params={"page_size": 1, "page": 2}).json()
    assert first["total"] == last["total"] == 2
    assert first["items"][0]["id"] != last["items"][0]["id"]
    assert client.get("/api/cases", params={"page_size": 101}).status_code == 422


def test_admission_respects_case_status(database):
    client = TestClient(app)
    content = os_content(client)
    case, version = create(client, "OS-001", {**content, "status": "disabled"})
    client.put(
        f"/api/cases/{case['id']}/stable",
        json={"version_id": version["id"], "expected_version_id": None},
    )
    assert (
        client.post(
            "/api/runs", json={"case_id": case["id"], "request_id": str(uuid4())}
        ).status_code
        == 409
    )


def test_applicability_matches_any_within_group_and_all_across_groups(database):
    content = {
        "required_tags": ["simulator"],
        "android_version_ids": ["15", "16"],
        "device_model_ids": ["phone"],
        "chip_platform_ids": ["chip"],
    }
    assert service.matches_device(
        content, ["simulator", "android_version:16", "device_model:phone", "chip_platform:chip"]
    )
    assert not service.matches_device(
        content, ["simulator", "android_version:17", "device_model:phone", "chip_platform:chip"]
    )
    assert not service.matches_device(
        content, ["simulator", "android_version:16", "device_model:phone"]
    )
    app_content = {"app_version_ids": ["app-v2", "app-v3"]}
    assert service.matches_device(app_content, ["app_version:app-v3"])
    assert not service.matches_device(app_content, ["android_version:app-v3"])
    assert not service.matches_device(app_content, ["app_version:another-app-v3"])


def test_unmatched_case_does_not_starve_following_case_and_start_rechecks_status(database):
    with database.begin() as conn:
        base = {
            "title": "test",
            "target": "android_os",
            "steps": ["操作"],
            "assertions": ["结果"],
            "required_tags": ["simulator"],
        }
        c1 = service.create_case(conn, "unmatched")
        v1 = service.add_version(conn, c1["id"], {**base, "required_tags": ["missing"]})
        service.set_stable(conn, c1["id"], v1["id"])
        service.submit(conn, SubmitRun(case_id=c1["id"], request_id=uuid4()))
        c2 = service.create_case(conn, "matched")
        v2 = service.add_version(conn, c2["id"], base)
        service.set_stable(conn, c2["id"], v2["id"])
        run = service.submit(conn, SubmitRun(case_id=c2["id"], request_id=uuid4()))
        db.execute(
            conn,
            "INSERT INTO nodes(id,token_hash,capabilities,last_seen,connection_generation) VALUES ('n','x','[\"simulator\"]',now(),1)",
        )
        db.execute(
            conn,
            "INSERT INTO devices(id,node_id,serial,tags) VALUES (:id,'n','SIM-001','[\"simulator\"]')",
            id=uuid4(),
        )
        assert service.schedule_one(conn)
        command = Command.model_validate(db.one(conn, "SELECT payload FROM commands")["payload"])
        chosen = db.one(
            conn,
            "SELECT cr.run_id FROM case_runs cr JOIN attempts a ON a.case_run_id=cr.id WHERE a.id=:id",
            id=command.attempt_id,
        )
        assert chosen["run_id"] == run["id"]
        disabled = service.add_version(conn, c2["id"], {**base, "status": "disabled"})
        service.set_stable(conn, c2["id"], disabled["id"])
        service.ingest_event(
            conn,
            "n",
            1,
            NodeEvent(
                seq=1,
                command_id=command.command_id,
                attempt_id=command.attempt_id,
                lease_generation=command.lease_generation,
                kind="ready",
            ),
        )
        assert (
            db.one(conn, "SELECT state FROM attempts WHERE id=:id", id=command.attempt_id)["state"]
            == "cancel_pending"
        )
        assert not db.one(conn, "SELECT id FROM commands WHERE payload->>'kind'='execute'")
