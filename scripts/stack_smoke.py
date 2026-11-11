"""Owns its subprocesses. Requires dev DB/MQ and free API port; never touches USB."""

import json
import time
from uuid import uuid4

import httpx
from dev import ROOT, Processes, environment


def wait(check, timeout=35):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            value = check()
            if value:
                return value
        except (httpx.TransportError, KeyError, IndexError):
            pass
        time.sleep(0.2)
    raise AssertionError("Timed out; inspect .runtime/logs")


def main():
    env = environment()
    env["SIM_DURATION"] = "8"
    directory = ROOT / f".runtime/smoke-{uuid4().hex}"
    env.pop("NODE_JOURNAL", None)
    env["NODE_STATE_DIR"] = str(directory)
    env["NODE_ID"] = "smoke-" + uuid4().hex[:10]
    stack = Processes(env)
    client = httpx.Client(base_url="http://127.0.0.1:8100", timeout=4)

    def post(path, body=None):
        r = client.post("/api" + path, json=body)
        r.raise_for_status()
        return r.json()

    def overview():
        r = client.get("/api/overview")
        r.raise_for_status()
        return r.json()

    try:
        for name in ["api", "scheduler", "publisher", "node"]:
            stack.start(name)
        wait(lambda: client.get("/readyz").is_success)
        wait(lambda: any(n["id"] == env["NODE_ID"] and n["last_seen"] for n in overview()["nodes"]))
        case = post("/cases", {"name": "M0 recovery " + uuid4().hex[:6]})
        version = post(
            f"/cases/{case['id']}/versions",
            {
                "title": "Stable before start",
                "target": "android_os",
                "steps": ["模拟打开设置"],
                "assertions": ["模拟检查界面"],
                "required_tags": ["simulator"],
            },
        )
        client.put(
            f"/api/cases/{case['id']}/stable",
            json={"version_id": version["id"], "expected_version_id": None},
        ).raise_for_status()
        run = post(
            "/runs",
            {
                "case_id": case["id"],
                "request_id": str(uuid4()),
                "artifacts": {"firmware": "demo-content-digest"},
            },
        )

        def attempt():
            return next((a for a in overview()["attempts"] if a["run_id"] == run["id"]), None)

        wait(lambda: attempt() and attempt()["state"] == "preparing")
        # Outbox is persisted; RabbitMQ holds work while the consumer is absent.
        stack.start("worker")
        wait(lambda: attempt()["state"] == "running")
        stack.stop("node", crash=True)
        stack.stop("api", crash=True)
        time.sleep(0.5)
        stack.start("api")
        wait(lambda: client.get("/healthz").is_success)
        stack.start("node")
        result = wait(lambda: attempt() if attempt()["state"] == "completed" else None)
        assert result["version_id"] == version["id"]
        assert result["verdict"] == "inconclusive"
        from control_plane.infrastructure import db

        with db.engine.connect() as conn:
            assert (
                db.one(
                    conn,
                    "SELECT count(*) AS n FROM commands WHERE attempt_id=:a AND launch_intent_at IS NOT NULL",
                    a=result["id"],
                )["n"]
                == 1
            )
            assert (
                db.one(
                    conn,
                    "SELECT count(*) AS n FROM node_events WHERE payload->>'attempt_id'=:a AND payload->>'kind'='started'",
                    a=result["id"],
                )["n"]
                == 1
            )
        assert not list(directory.rglob("*.sqlite*"))
        assert not any(d["attempt_id"] == result["id"] for d in overview()["devices"])
        # A subsequent task is cancelled only after node cleanup, then its lease is released.
        second = post("/runs", {"case_id": case["id"], "request_id": str(uuid4())})

        def next_attempt():
            return next(a for a in overview()["attempts"] if a["run_id"] == second["id"])

        wait(lambda: next_attempt()["state"] == "running")
        post("/attempts/" + next_attempt()["id"] + "/cancel")
        wait(lambda: next_attempt()["state"] == "cancelled")
        report = {
            "passed": True,
            "checks": [
                "real RabbitMQ publisher/worker delivery",
                "worker absent then starts",
                "API SIGKILL/restart",
                "node runtime SIGKILL/restart",
                "runtime recovers from central admission",
                "single execution per attempt",
                "event replay",
                "verified cleanup before lease release",
                "running cancellation",
            ],
            "attempt_id": result["id"],
            "node_id": env["NODE_ID"],
            "simulation": True,
        }
        path = ROOT / ".runtime/stack-smoke.json"
        path.write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report, indent=2))
    finally:
        client.close()
        stack.close()


if __name__ == "__main__":
    main()
