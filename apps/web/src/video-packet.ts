export type VideoPacket =
  | { type: "configuration"; data: Uint8Array }
  | { type: "data"; data: Uint8Array; keyframe: boolean; pts: bigint };

export function parseVideoPacket(buffer: ArrayBuffer): VideoPacket {
  const view = new DataView(buffer);
  if (buffer.byteLength <= 12 || view.getUint32(8) !== buffer.byteLength - 12)
    throw new Error("视频包格式错误");
  const flags = view.getBigUint64(0);
  const data = new Uint8Array(buffer, 12);
  return flags & (1n << 63n)
    ? { type: "configuration", data }
    : {
        type: "data",
        data,
        keyframe: !!(flags & (1n << 62n)),
        pts: flags & ((1n << 62n) - 1n),
      };
}

/** Retain SPS/PPS and start every new stream at an IDR, including after rotation. */
export class MsePackets {
  private config?: Uint8Array;
  private started = false;
  private pts?: bigint;

  configure(data: Uint8Array) {
    const changed =
      !this.config ||
      this.config.length !== data.length ||
      this.config.some((value, i) => value !== data[i]);
    if (changed) {
      this.config = data.slice();
      this.started = false;
      this.pts = undefined;
    }
    return changed;
  }

  frame(packet: Extract<VideoPacket, { type: "data" }>) {
    if (!this.config || (!this.started && !packet.keyframe)) return;
    let video = packet.data;
    if (!this.started) {
      video = new Uint8Array(this.config.length + packet.data.length);
      video.set(this.config);
      video.set(packet.data, this.config.length);
    }
    // PTS is in microseconds. Compress long static-screen gaps for live playback.
    const delta =
      this.pts === undefined ? 33 : Number(packet.pts - this.pts) / 1000;
    const duration = Math.max(
      1,
      Math.min(250, Math.round(delta > 0 ? delta : 33)),
    );
    this.pts = packet.pts;
    this.started = true;
    return { video, duration, isLastVideoFrameComplete: true };
  }
}
