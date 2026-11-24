"""Live H.264 fan-out for a single API process; no media enters PostgreSQL."""

from dataclasses import dataclass, field


@dataclass
class Channel:
    producer: object = None
    viewers: set = field(default_factory=set)
    config: bytes | None = None
    gop: list = field(default_factory=list)
    gop_bytes: int = 0
    gop_valid: bool = False
    status: dict = field(default_factory=lambda: {"state": "waiting", "detail": "等待节点视频通道"})

    async def demand(self):
        if self.producer:
            await self.producer.send_json({"type": "viewers", "count": len(self.viewers)})

    def publish(self, value):
        for queue in tuple(self.viewers):
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait({"state": "error", "detail": "播放端积压，请重新连接"})
                self.viewers.discard(queue)
            else:
                queue.put_nowait(value)

    def packet(self, packet):
        flags = int.from_bytes(packet[:8], "big")
        if flags & (1 << 63):
            self.config = packet
            self.gop = []
            self.gop_bytes = 0
            self.gop_valid = False
        else:
            if flags & (1 << 62):
                self.gop = []
                self.gop_bytes = 0
                self.gop_valid = True
            if self.gop_valid and self.gop_bytes + len(packet) <= 8_000_000 and len(self.gop) < 600:
                self.gop.append(packet)
                self.gop_bytes += len(packet)
            else:
                self.gop = []
                self.gop_bytes = 0
                self.gop_valid = False
        self.publish(packet)


channels = {}


def channel(device_id):
    return channels.setdefault(str(device_id), Channel())
