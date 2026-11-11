"""Node-owned, demand-driven H.264 stream. Input remains lease guarded elsewhere."""

import asyncio
import json
import os
import re
import secrets
import select
import socket
import struct
import subprocess
import time
from collections import deque
from pathlib import Path

import av
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake


class VideoSource:
    def __init__(self, serial):
        self.serial = serial
        self.backend = os.getenv("VIDEO_BACKEND", "scrcpy")
        self.process = None
        self.socket = None
        self.port = None
        self.remote = None
        self.pidfile = None
        self.pending = deque()
        self.parser = None
        self.config = None
        self.buffer = b""

    def adb(self, *args):
        return subprocess.check_output(
            ["adb", "-s", self.serial, *args], stderr=subprocess.DEVNULL, timeout=10
        )

    def __enter__(self):
        try:
            if self.backend == "screenrecord":
                self.parser = av.CodecContext.create("h264", "r")
                tag = secrets.token_hex(8)
                self.pidfile = f"/data/local/tmp/platform-video-{tag}.pid"
                viewport = self.adb("shell", "dumpsys", "input").decode()
                match = re.search(
                    r"Viewport INTERNAL: displayId=0,[^\n]*logicalFrame=\[0, 0, (\d+), (\d+)\]",
                    viewport,
                )
                if not match:
                    raise RuntimeError("无法读取屏幕尺寸")
                width, height = map(int, match.groups())
                scale = min(1, 1280 / max(width, height))
                width, height = (max(16, round(v * scale / 16) * 16) for v in (width, height))
                limit = int(os.getenv("VIDEO_SCREENRECORD_TIME_LIMIT", "180"))
                if limit < 0 or limit > 180:
                    raise ValueError("VIDEO_SCREENRECORD_TIME_LIMIT must be 0..180")
                command = f"echo $$ > {self.pidfile}; exec screenrecord --output-format=h264 --size {width}x{height} --bit-rate 4000000 --time-limit {limit} -"
                self.process = subprocess.Popen(
                    ["adb", "-s", self.serial, "exec-out", "sh", "-c", command],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                )
            elif self.backend == "scrcpy":
                path = os.getenv("VIDEO_SCRCPY_SERVER_PATH")
                if not path:
                    raise RuntimeError("请配置 VIDEO_SCRCPY_SERVER_PATH")
                version = os.getenv("VIDEO_SCRCPY_VERSION", "3.3.3")
                tag = f"{secrets.randbelow(0x7FFFFFFF):08x}"
                self.remote = f"/data/local/tmp/platform-video-{tag}.jar"
                self.adb("push", str(Path(path).resolve()), self.remote)
                self.port = (
                    self.adb("forward", "tcp:0", f"localabstract:scrcpy_{tag}").decode().strip()
                )
                command = f"CLASSPATH={self.remote} app_process / com.genymobile.scrcpy.Server {version} scid={tag} audio=false control=false tunnel_forward=true send_device_meta=false max_size=1280 max_fps=30 video_bit_rate=4000000 video_codec_options=i-frame-interval:int=1"
                self.process = subprocess.Popen(
                    ["adb", "-s", self.serial, "shell", command],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                for _ in range(30):
                    try:
                        candidate = socket.create_connection(
                            ("127.0.0.1", int(self.port)), timeout=0.5
                        )
                    except OSError:
                        time.sleep(0.1)
                        continue
                    try:
                        if candidate.recv(1) == b"\0":
                            self.socket = candidate
                            break
                    except OSError:
                        pass
                    candidate.close()
                    time.sleep(0.1)
                if not self.socket:
                    raise RuntimeError("scrcpy 视频握手失败")
                self.socket.settimeout(6)
                header = self.exact(12)
                if header[:4] != b"h264":
                    raise RuntimeError("只支持 H.264 视频")
                self.socket.setblocking(False)
            else:
                raise ValueError("VIDEO_BACKEND must be scrcpy or screenrecord")
            return self
        except BaseException:
            self.close()
            raise

    def exact(self, n):
        data = b""
        while len(data) < n:
            chunk = self.socket.recv(n - len(data))
            if not chunk:
                raise EOFError("视频通道已结束")
            data += chunk
        return data

    def read(self):
        if self.pending:
            return self.pending.popleft()
        if self.backend == "scrcpy":
            if select.select([self.socket], [], [], 0.2)[0]:
                chunk = self.socket.recv(65536)
                if not chunk:
                    raise EOFError("scrcpy 视频通道已结束")
                self.buffer += chunk
            if len(self.buffer) >= 12:
                length = int.from_bytes(self.buffer[8:12], "big")
                if length > 2_000_000:
                    raise ValueError("视频包过大")
                if len(self.buffer) >= length + 12:
                    packet = self.buffer[: length + 12]
                    self.buffer = self.buffer[length + 12 :]
                    return packet
            return None
        if select.select([self.process.stdout], [], [], 0.15)[0]:
            chunk = self.process.stdout.read1(65536)
            if not chunk:
                raise EOFError("原生录屏已结束")
        else:
            chunk = b""
        for packet in self.parser.parse(chunk):
            data = bytes(packet)
            nals = [v for v in re.split(b"\x00\x00\x00?\x01", data) if v]
            config = b"".join(b"\0\0\0\1" + n for n in nals if n[0] & 31 in (7, 8))
            if config and config != self.config:
                self.config = config
                self.pending.append(struct.pack(">QI", 1 << 63, len(config)) + config)
            if any(n[0] & 31 in (1, 5) for n in nals):
                key = any(n[0] & 31 == 5 for n in nals)
                flags = (time.monotonic_ns() // 1000) | ((1 << 62) if key else 0)
                self.pending.append(struct.pack(">QI", flags, len(data)) + data)
        return self.pending.popleft() if self.pending else None

    def close(self):
        if self.socket:
            self.socket.close()
        if self.pidfile and self.process and self.process.poll() is None:
            try:
                self.adb(
                    "shell",
                    f'if [ -f {self.pidfile} ]; then read p < {self.pidfile}; kill -INT "$p" 2>/dev/null; fi',
                )
            except (OSError, subprocess.SubprocessError):
                pass
        if self.process:
            self.process.terminate()
            try:
                self.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        if self.port:
            try:
                self.adb("forward", "--remove", "tcp:" + self.port)
            except (OSError, subprocess.SubprocessError):
                pass
        for path in (self.remote, self.pidfile):
            if path:
                try:
                    self.adb("shell", "rm", "-f", path)
                except (OSError, subprocess.SubprocessError):
                    pass

    def __exit__(self, *args):
        self.close()


async def video_transport(base, node, serial):
    uri = (
        base.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        + f"/api/nodes/{node}/video/{serial}"
    )
    while True:
        try:
            async with connect(uri, max_size=1024) as ws:
                demand = asyncio.Event()

                async def receive():
                    async for message in ws:
                        value = json.loads(message)
                        if value.get("type") == "viewers":
                            if value["count"] > 0:
                                demand.set()
                            else:
                                demand.clear()
                    raise ConnectionError("Video gateway disconnected")

                async def send():
                    while True:
                        await demand.wait()
                        await ws.send(
                            json.dumps(
                                {
                                    "state": "starting",
                                    "detail": "正在启动视频",
                                    "backend": os.getenv("VIDEO_BACKEND", "scrcpy"),
                                }
                            )
                        )
                        source = VideoSource(serial)
                        try:
                            await asyncio.to_thread(source.__enter__)
                            started = time.monotonic()
                            last_status = 0
                            frames = 0
                            while demand.is_set():
                                packet = await asyncio.to_thread(source.read)
                                if packet:
                                    await ws.send(packet)
                                    if not int.from_bytes(packet[:8], "big") & (1 << 63):
                                        frames += 1
                                if not frames and time.monotonic() - started > 8:
                                    raise RuntimeError("设备未输出视频帧，请检查采集兼容性")
                                if time.monotonic() - last_status > 2:
                                    await ws.send(
                                        json.dumps(
                                            {
                                                "state": "streaming" if frames else "starting",
                                                "detail": "等待设备画面更新" if not frames else "",
                                                "backend": source.backend,
                                                "frames": frames,
                                            }
                                        )
                                    )
                                    last_status = time.monotonic()
                        except (
                            OSError,
                            ValueError,
                            EOFError,
                            RuntimeError,
                            subprocess.SubprocessError,
                            av.FFmpegError,
                        ) as exc:
                            await ws.send(json.dumps({"state": "error", "detail": str(exc)[:200]}))
                            await asyncio.sleep(2)
                        finally:
                            await asyncio.to_thread(source.close)
                        if not demand.is_set():
                            await ws.send(json.dumps({"state": "stopped"}))

                sender = asyncio.create_task(send())
                receiver = asyncio.create_task(receive())
                try:
                    done, pending = await asyncio.wait(
                        [sender, receiver], return_when=asyncio.FIRST_COMPLETED
                    )
                    for task in pending:
                        task.cancel()
                    await asyncio.gather(*done, *pending, return_exceptions=True)
                finally:
                    sender.cancel()
                    receiver.cancel()
                    await asyncio.gather(sender, receiver, return_exceptions=True)
        except (OSError, ValueError, ConnectionError, ConnectionClosed, InvalidHandshake):
            pass
        await asyncio.sleep(2)
