"""Real HTTP/process recovery, isolated PostgreSQL schema, no hardware or broker."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from control_plane.infrastructure import db
from test_foundation import prepare, seed

pytestmark = pytest.mark.integration


def wait(check, timeout=15):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if result := check():
                return result
        except (httpx.HTTPError, FileNotFoundError):
            pass
        time.sleep(0.05)
    raise AssertionError("Timed out waiting for isolated node process")


def test_api_outage_offline_completion_and_node_process_restart(database, tmp_path):
    seed(database)
    prepare(database)
    with database.connect() as conn:
        schema = conn.exec_driver_sql("select current_schema()").scalar_one()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    url = database.url.update_query_dict({"options": f"-csearch_path={schema}"}).render_as_string(
        hide_password=False
    )
    base = f"http://127.0.0.1:{port}"
    state = tmp_path / "node"
    env = {
        **os.environ,
        "DATABASE_URL": url,
        "API_URL": base,
        "AUTH_MODE": "open",
        "NODE_ID": "n",
        "NODE_STATE_DIR": str(state),
        "SIM_DEVICE_SERIALS": '["SIM-001"]',
        "SIM_DURATION": "3",
    }
    env.pop("NODE_JOURNAL", None)
    children = []
    root = Path(__file__).resolve().parents[2]
    api_args = [
        sys.executable,
        "-m",
        "uvicorn",
        "control_plane.entrypoints.api:app",
        "--port",
        str(port),
        "--host",
        "127.0.0.1",
    ]
    node_args = [sys.executable, "-m", "node_agent.transport.client"]
    with (
        (tmp_path / "process.log").open("w") as log,
        httpx.Client(base_url=base, timeout=0.5, trust_env=False) as client,
    ):

        def launch(args):
            child = subprocess.Popen(args, cwd=root, env=env, stdout=log, stderr=log)
            children.append(child)
            return child

        def status():
            with database.begin() as conn:
                db.execute(conn, "UPDATE commands SET dispatch_ready=true")
                return db.one(conn, "SELECT state FROM attempts")["state"]

        try:
            api = launch(api_args)
            wait(lambda: client.get("/healthz").is_success)
            node = launch(node_args)
            wait(lambda: status() == "running")
            api.kill()
            api.wait(timeout=5)

            def offline_result():
                return any(
                    json.loads(p.read_text())["event"]["kind"] == "finished"
                    for p in (state / "events").glob("*.json")
                )

            wait(offline_result)
            assert status() == "running"
            with database.connect() as conn:
                assert db.one(conn, "SELECT released_at FROM leases")["released_at"] is None
            node.kill()
            node.wait(timeout=5)
            launch(api_args)
            wait(lambda: client.get("/healthz").is_success)
            launch(node_args)
            wait(lambda: status() == "completed")
            wait(lambda: not list((state / "events").glob("*.json")))
            with database.connect() as conn:
                assert (
                    db.one(
                        conn,
                        "SELECT count(*) AS n FROM commands WHERE launch_intent_at IS NOT NULL",
                    )["n"]
                    == 1
                )
                assert db.one(conn, "SELECT released_at FROM leases")["released_at"] is not None
                assert db.one(conn, "SELECT runtime_protocol FROM nodes")["runtime_protocol"] == 2
            assert not list(state.rglob("*.sqlite*"))
        finally:
            for child in reversed(children):
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait(timeout=5)
