import json
from types import SimpleNamespace
from uuid import uuid4

import pytest
from node_agent.config import simulation_registration
from node_agent.spool import Spool
from node_agent.supervisor.simulator import Simulator
from platform_contracts.models import Command


def test_default_and_twenty_device_inventory():
    assert simulation_registration({}).device_serials == ["SIM-001", "SIM-002"]
    serials = [f"SIM-N2-{i:03}" for i in range(1, 21)]
    inventory = simulation_registration(
        {
            "NODE_ID": "lab-node-2",
            "SIM_DEVICE_SERIALS": json.dumps(serials),
            "SIM_DEVICE_TAGS": '["simulator", "lab-node-2"]',
        }
    )
    assert inventory.node_id == "lab-node-2" and inventory.device_serials == serials
    assert inventory.tags == ["simulator", "lab-node-2"]


@pytest.mark.parametrize(
    "value",
    [
        "not-json",
        '"SIM-001"',
        "[]",
        '["real-usb-serial"]',
        '["SIM-001","SIM-001"]',
        "[null]",
        "[1]",
        '["SIM- bad"]',
        json.dumps([f"SIM-{i}" for i in range(101)]),
    ],
)
def test_invalid_inventory_fails_before_registration(value):
    with pytest.raises(ValueError):
        simulation_registration({"SIM_DEVICE_SERIALS": value})


@pytest.mark.parametrize("value", ["[]", '"simulator"', '["other"]', '["simulator",null]'])
def test_inventory_tags_remain_explicitly_simulated(value):
    with pytest.raises(ValueError):
        simulation_registration({"SIM_DEVICE_TAGS": value})


def test_node_identity_is_pinned_across_reopen(tmp_path):
    Spool(tmp_path, "n1")
    Spool(tmp_path, "n1")
    with pytest.raises(ValueError, match="another node"):
        Spool(tmp_path, "n2")


def command(node="n1", serial="SIM-001"):
    from datetime import datetime, timedelta, timezone

    return Command(
        command_id=uuid4(),
        attempt_id=uuid4(),
        node_id=node,
        device_id=uuid4(),
        serial=serial,
        lease_generation=1,
        kind="prepare",
        expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
    )


@pytest.mark.parametrize("node,serial", [("n2", "SIM-001"), ("n1", "SIM-999")])
def test_supervisor_rejects_other_node_or_undeclared_serial(tmp_path, node, serial):
    spool = Spool(tmp_path, "n1")
    runtime = SimpleNamespace(spool=spool, online=True)
    simulator = Simulator(runtime, registration=simulation_registration({"NODE_ID": "n1"}))
    cmd = command(node, serial)
    simulator.recover(
        [
            {
                "attempt_id": str(cmd.attempt_id),
                "state": "preparing",
                "command": cmd.model_dump(mode="json"),
                "launch_intent_at": None,
            }
        ]
    )
    try:
        with pytest.raises(ValueError, match="device_not_in_node_inventory"):
            simulator.tick()
        assert not spool.events()
        assert not simulator.locks
    finally:
        simulator.close()
