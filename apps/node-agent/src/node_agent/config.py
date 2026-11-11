"""Explicit simulator inventory; real-device discovery remains a separate adapter."""

import json
import os
import re

from platform_contracts.models import NodeRegistration


def simulation_registration(env=None):
    env = os.environ if env is None else env
    try:
        serials = json.loads(env.get("SIM_DEVICE_SERIALS", '["SIM-001","SIM-002"]'))
        tags = json.loads(env.get("SIM_DEVICE_TAGS", '["simulator"]'))
    except (TypeError, json.JSONDecodeError):
        raise ValueError("SIM_DEVICE_SERIALS and SIM_DEVICE_TAGS must be JSON arrays") from None
    if (
        not isinstance(serials, list)
        or not 1 <= len(serials) <= 100
        or any(
            not isinstance(s, str) or not re.fullmatch(r"SIM-[A-Za-z0-9_-]{1,100}", s)
            for s in serials
        )
        or len(set(serials)) != len(serials)
    ):
        raise ValueError("SIM_DEVICE_SERIALS requires 1..100 unique SIM- serials")
    if (
        not isinstance(tags, list)
        or not 1 <= len(tags) <= 30
        or any(not isinstance(t, str) or not t.strip() or len(t) > 100 for t in tags)
        or "simulator" not in tags
    ):
        raise ValueError("SIM_DEVICE_TAGS requires nonempty tags including simulator")
    return NodeRegistration(
        node_id=env.get("NODE_ID", "sim-linux-01"), device_serials=serials, tags=tags
    )
