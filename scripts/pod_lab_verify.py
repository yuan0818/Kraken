"""Real containers, HTTPS and broker; simulated devices and verdicts only."""

import json
import time
from collections import Counter
from datetime import datetime, timezone
from uuid import uuid4

from pod_lab import podman, wait


def container_python(lab, suffix, source, *args):
    name = lab.resource(suffix)
    if not lab.inspect("container", name):
        raise RuntimeError(f"Missing owned container {name}")
    output = podman("exec", name, "python", "-c", source, *args).stdout
    return json.loads(output)


def query(lab, sql, **params):
    return container_python(
        lab,
        "api",
        """
import json, sys
from control_plane.infrastructure import db
with db.engine.connect() as conn:
    print(json.dumps(db.rows(conn, sys.argv[1], **json.loads(sys.argv[2])), default=str))
""",
        sql,
        json.dumps(params),
    )


def node_state(lab, node):
    files = container_python(
        lab,
        f"node-{node}-agent",
        """
import json
from pathlib import Path
root = Path('/state')
events = [json.loads(p.read_text())["event"] for p in (root / 'events').glob('*.json')]
print(json.dumps({'pending': len(events), 'events': events, 'owner': json.loads((root / 'identity.json').read_text())['node_id']}))
""",
    )
    files["jobs"] = query(
        lab,
        """SELECT a.id AS attempt,a.state,
        count(c.launch_intent_at) AS starts FROM attempts a
        JOIN commands c ON c.attempt_id=a.id WHERE c.node_id=:n
        GROUP BY a.id,a.state""",
        n=lab.resource(f"node-{node}"),
    )
    return files


def verify(lab):
    report = {
        "passed": False,
        "simulation": True,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "checks": [],
        "batches": [],
        "nodes": 3,
        "devices_per_node": lab.config["devices"],
        "not_validated": [
            "real Artemis/model calls",
            "USB/ADB/Fastboot",
            "systemd/host reboot",
            "physical capacity",
        ],
    }
    prefix = "LAB-" + uuid4().hex[:8]
    client = lab.client()
    disconnected = None
    selected_versions = {}

    def api(method, path, body=None):
        response = client.request(method, "/api" + path, json=body)
        response.raise_for_status()
        return response.json()

    def note(name, **details):
        report["checks"].append({"check": name, **details})
        print(f"OK: {name}", flush=True)

    def overview():
        return api("GET", "/overview")

    def case(node, number):
        record = api(
            "POST",
            "/cases",
            {"name": f"{prefix} node {node} case {number}", "code": f"{prefix}-{node}-{number}"},
        )
        version = api(
            "POST",
            f"/cases/{record['id']}/versions",
            {
                "title": "Pod lab simulation",
                "target": "android_os",
                "steps": ["模拟打开设置"],
                "assertions": ["需要真实设备证据才可判定通过"],
                "required_tags": ["simulator", f"lab-node-{node}"],
            },
        )
        api(
            "PUT",
            f"/cases/{record['id']}/stable",
            {"version_id": version["id"], "expected_version_id": None},
        )
        selected_versions[record["id"]] = version["id"]
        return record["id"]

    def batch(case_ids, retries=0):
        task = api("POST", "/tasks", {"name": prefix + " validation", "request_id": str(uuid4())})
        task = api(
            "PUT",
            f"/tasks/{task['id']}",
            {
                "expected_revision": task["revision"],
                "name": task["name"],
                "case_ids": case_ids,
                "configuration": {
                    "max_parallel": len(case_ids),
                    "max_retries": retries,
                    "retry_interval_seconds": 1,
                },
            },
        )
        result = api(
            "POST",
            f"/tasks/{task['id']}/execute",
            {"expected_revision": task["revision"], "request_id": str(uuid4())},
        )
        report["batches"].append(result["id"])
        return result["id"]

    def detail(run):
        return api("GET", f"/executions/{run}")

    def attempts(run):
        return [a for c in detail(run)["cases"] for a in c["attempts"]]

    def running(run, count=1):
        values = attempts(run)
        return values if sum(a["state"] == "running" for a in values) == count else None

    def done(run, expected_attempts, state="completed"):
        result = wait(
            lambda: value if (value := detail(run))["state"] == state else None,
            f"batch {run} {state}",
            timeout=lab.config["duration"] * 3 + 60,
        )
        values = [a for c in result["cases"] for a in c["attempts"]]
        assert len(values) == expected_attempts, (len(values), expected_attempts)
        assert all(a["simulation"] and a["cleanup"] == "verified" for a in values)
        if state == "completed":
            assert all(
                a["verdict"] == "inconclusive" and a["evidence"] == "partial" for a in values
            )
            assert all(
                a["version_id"] == selected_versions[c["case_id"]]
                for c in result["cases"]
                for a in c["attempts"]
            )
        leases = query(
            lab,
            "SELECT l.released_at FROM leases l JOIN attempts a ON a.id=l.attempt_id JOIN case_runs cr ON cr.id=a.case_run_id WHERE cr.run_id=:run",
            run=run,
        )
        assert all(row["released_at"] is not None for row in leases)
        return result

    try:
        inventory = wait(
            lambda: (
                data if len((data := overview())["devices"]) == 3 * lab.config["devices"] else None
            ),
            "registered inventory",
        )
        assert Counter(d["node_id"] for d in inventory["devices"]) == {
            lab.resource(f"node-{i}"): lab.config["devices"] for i in range(1, 4)
        }
        assert all(n["last_seen"] for n in inventory["nodes"])
        if any(d["attempt_id"] for d in inventory["devices"]) or any(
            a["state"] not in {"completed", "cancelled", "blocked"} for a in inventory["attempts"]
        ):
            raise RuntimeError(
                "Lab has unfinished work; finish or reconcile it before verification"
            )
        bad = client.post(
            "/api/nodes/register",
            json={"node_id": lab.resource("node-1"), "device_serials": ["real-device"]},
        )
        assert bad.status_code == 422
        note(
            "three Linux nodes registered over HTTPS without tokens; invalid simulator inventory rejected"
        )
        for i in range(1, 4):
            assert node_state(lab, i)["owner"] == lab.resource(f"node-{i}")
            locked = container_python(
                lab,
                f"node-{i}-agent",
                """
import json
from node_agent.devices.lock import LocalLock
try:
    lock = LocalLock('/state/runtime.lock')
except BlockingIOError:
    print('true')
else:
    lock.close()
    print('false')
""",
            )
            assert locked
        note("separate persistent node identities and OS-enforced runtime locks")

        print("Creating parallel workload", flush=True)
        cases = [case(i, j) for j in range(1, lab.config["devices"] + 1) for i in range(1, 4)]
        run = batch(cases)
        wait(
            lambda: running(run, len(cases)),
            "all simulated slots running simultaneously",
            timeout=lab.config["duration"],
        )
        active = query(
            lab,
            "SELECT d.node_id,count(*) AS n FROM leases l JOIN devices d ON d.id=l.device_id WHERE l.released_at IS NULL GROUP BY d.node_id",
        )
        assert {row["node_id"]: row["n"] for row in active} == {
            lab.resource(f"node-{i}"): lab.config["devices"] for i in range(1, 4)
        }
        note("simultaneous exclusive simulated device leases", active_by_node=active)

        lab.restart("node-1-agent")
        note("node runtime restart recovers active attempts from control plane")

        name = lab.resource("node-2-agent")
        lab.inspect("container", name)
        try:
            podman("kill", "--signal", "KILL", name)
        finally:
            podman("start", name)
        note("runtime SIGKILL/restart uses central launch admissions")

        old_pod = lab.inspect("pod", lab.resource("node-3"))["Id"]
        try:
            podman("pod", "rm", "-f", lab.resource("node-3"))
        finally:
            lab.start_node(3)
        assert lab.inspect("pod", lab.resource("node-3"))["Id"] != old_pod
        lab.restart("api")
        wait(lambda: client.get("/readyz").is_success, "restarted API")
        done(run, len(cases))
        attempt_ids = {a["id"] for a in attempts(run)}
        for i in range(1, 4):
            jobs = [j for j in node_state(lab, i)["jobs"] if j["attempt"] in attempt_ids]
            assert len(jobs) == lab.config["devices"] and all(
                j["starts"] == 1 and j["state"] == "completed" for j in jobs
            )
        note(
            "Pod replacement and API restart preserve snapshots, single starts, terminal results and released leases"
        )

        print("Injecting isolated node network loss", flush=True)
        offline_run = batch([cases[0]])
        wait(lambda: running(offline_run), "offline scenario started")
        attempt_id = attempts(offline_run)[0]["id"]
        disconnected = lab.inspect("pod", lab.resource("node-1"))["InfraContainerID"]
        podman("network", "disconnect", lab.resource("network"), disconnected)
        other_run = batch([cases[1]])
        wait(lambda: running(other_run), "another node still accepts work")

        def stale():
            node = next(n for n in overview()["nodes"] if n["id"] == lab.resource("node-1"))
            return (
                datetime.now(timezone.utc)
                - datetime.fromisoformat(node["last_seen"].replace("Z", "+00:00"))
            ).total_seconds() > 16

        wait(stale, "disconnected node heartbeat expires", timeout=40)
        queued_run = batch([cases[0]])
        # Allow several real scheduler ticks; a stale node must receive no new lease.
        time.sleep(2)
        assert attempts(queued_run)[0]["state"] == "queued"
        api("POST", f"/executions/{queued_run}/cancel")
        done(queued_run, 1, "cancelled")
        wait(
            lambda: any(
                j["attempt_id"] == attempt_id and j["kind"] == "finished"
                for j in node_state(lab, 1)["events"]
            ),
            "runtime persists offline completion",
            timeout=lab.config["duration"] + 20,
        )
        assert node_state(lab, 1)["pending"] > 0
        assert any(d["attempt_id"] == attempt_id for d in overview()["devices"])
        assert attempts(offline_run)[0]["state"] == "running"
        podman(
            "network",
            "connect",
            "--alias",
            lab.resource("node-1"),
            lab.resource("network"),
            disconnected,
        )
        disconnected = None
        done(offline_run, 1)
        done(other_run, 1)
        wait(lambda: node_state(lab, 1)["pending"] == 0, "offline events acknowledged")
        note(
            "network loss retains lease, blocks stale-node allocation, leaves other nodes active and replays offline completion"
        )

        print("Validating cancellation and bounded retry", flush=True)
        retry_run = batch([cases[0]], retries=1)
        cancel_run = batch([cases[2]])
        wait(lambda: running(cancel_run), "cancellation scenario started")
        api("POST", f"/executions/{cancel_run}/cancel")
        done(cancel_run, 1, "cancelled")
        retried = done(retry_run, 2)
        assert sorted(a["number"] for c in retried["cases"] for a in c["attempts"]) == [1, 2]
        assert not any(d["attempt_id"] for d in overview()["devices"])
        for i in range(1, 4):
            assert all(j["starts"] <= 1 for j in node_state(lab, i)["jobs"])
        note(
            "running cancellation cleans up; configured retry creates one new attempt; all leases released"
        )
        report["passed"] = True
    except BaseException as exc:
        report["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        try:
            if disconnected:
                podman(
                    "network",
                    "connect",
                    "--alias",
                    lab.resource("node-1"),
                    lab.resource("network"),
                    disconnected,
                )
        finally:
            client.close()
            report["finished_at"] = datetime.now(timezone.utc).isoformat()
            path = lab.directory / f"verification-{prefix}.json"
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
            print(f"Evidence: {path}", flush=True)


if __name__ == "__main__":
    raise SystemExit("Use scripts/pod_lab.py verify")
