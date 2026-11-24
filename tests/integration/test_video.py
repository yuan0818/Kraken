import asyncio
import struct
from uuid import uuid4

import pytest
from control_plane.entrypoints.api import app
from control_plane.modules.video import Channel, channels
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


def packet(flags, body=b"frame"):
    return struct.pack(">QI", flags, len(body)) + body


def test_video_stream_fans_out_and_releases_demand(database):
    channels.clear()
    with TestClient(app) as c:
        did = c.post(
            "/api/nodes/adb-inventory",
            json={
                "node_id": "video",
                "serial": "explicit",
                "state": "device",
                "artemis_state": "ready",
            },
        ).json()["id"]
        with c.websocket_connect("/api/nodes/video/video/explicit") as node:
            assert node.receive_json() == {"type": "viewers", "count": 0}
            with c.websocket_connect(f"/api/devices/{did}/video") as viewer:
                assert viewer.receive_json()["state"] == "waiting"
                assert node.receive_json()["count"] == 1
                config, key = packet(1 << 63), packet(1 << 62)
                node.send_bytes(config)
                node.send_bytes(key)
                assert viewer.receive_bytes() == config
                assert viewer.receive_bytes() == key
                with c.websocket_connect(f"/api/devices/{did}/video") as second:
                    second.receive_json()
                    assert second.receive_bytes() == config
                    assert second.receive_bytes() == key
                    assert node.receive_json()["count"] == 2
                assert node.receive_json()["count"] == 1
            assert node.receive_json()["count"] == 0


def test_video_rejects_unknown_devices_and_node_identity(database):
    with TestClient(app) as c:
        for url in [
            f"/api/devices/{uuid4()}/video",
            "/api/nodes/other/video/explicit",
        ]:
            with pytest.raises(WebSocketDisconnect):
                with c.websocket_connect(url):
                    pass


def test_slow_viewer_is_bounded_and_must_reconnect():
    media = Channel()
    slow = asyncio.Queue(maxsize=1)
    fast = asyncio.Queue(maxsize=4)
    media.viewers.update([slow, fast])
    media.packet(packet(1 << 63))
    media.packet(packet(1 << 62))
    assert slow not in media.viewers
    assert slow.get_nowait()["state"] == "error"
    assert fast.qsize() == 2


def test_invalid_video_packet_disconnects_producer(database):
    channels.clear()
    with TestClient(app) as c:
        c.post(
            "/api/nodes/adb-inventory",
            json={
                "node_id": "video",
                "serial": "explicit",
                "state": "device",
                "artemis_state": "ready",
            },
        )
        with c.websocket_connect("/api/nodes/video/video/explicit") as node:
            node.receive_json()
            node.send_bytes(b"bad packet")
            with pytest.raises(WebSocketDisconnect):
                node.receive_bytes()


def test_cache_never_replays_delta_frames_without_keyframe():
    media = Channel()
    media.packet(packet(0))
    assert not media.gop
    media.packet(packet(1 << 62))
    for _ in range(600):
        media.packet(packet(0))
    assert not media.gop and not media.gop_valid
    media.packet(packet(0))
    assert not media.gop
    media.packet(packet(1 << 62))
    assert media.gop_valid and len(media.gop) == 1


def test_encoder_restart_keeps_viewer_connected(database):
    channels.clear()
    with TestClient(app) as c:
        did = c.post(
            "/api/nodes/adb-inventory",
            json={
                "node_id": "video",
                "serial": "explicit",
                "state": "device",
                "artemis_state": "ready",
            },
        ).json()["id"]
        with c.websocket_connect("/api/nodes/video/video/explicit") as node:
            node.receive_json()
            with c.websocket_connect(f"/api/devices/{did}/video") as viewer:
                viewer.receive_json()
                node.receive_json()
                node.send_json({"state": "error", "detail": "encoder interrupted"})
                assert viewer.receive_json()["state"] == "error"
                node.send_json({"state": "starting"})
                assert viewer.receive_json()["state"] == "starting"
                key = packet(1 << 62)
                node.send_bytes(key)
                assert viewer.receive_bytes() == key
