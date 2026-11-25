import { test } from "node:test";
import assert from "node:assert/strict";
import { MsePackets, parseVideoPacket } from "../src/video-packet.ts";

function packet(flags: bigint, payload: number[]) {
  const data = new ArrayBuffer(12 + payload.length);
  const view = new DataView(data);
  view.setBigUint64(0, flags);
  view.setUint32(8, payload.length);
  new Uint8Array(data, 12).set(payload);
  return data;
}
const frame = (pts: bigint, keyframe = false) => ({
  type: "data" as const,
  data: new Uint8Array([0, 0, 1, keyframe ? 0x65 : 0x41]),
  pts,
  keyframe,
});

test("scrcpy configuration and IDR flags do not leak into microsecond timestamps", () => {
  assert.equal(parseVideoPacket(packet(1n << 63n, [7])).type, "configuration");
  const value = parseVideoPacket(packet((1n << 62n) | 123456789012n, [5]));
  assert.equal(value.type, "data");
  if (value.type === "data") {
    assert.equal(value.pts, 123456789012n);
    assert.equal(value.keyframe, true);
  }
  for (const length of [0, 11, 12, 13])
    assert.throws(() => parseVideoPacket(new ArrayBuffer(length)));
});

test("late joining waits for SPS/PPS and an IDR, then prepends configuration only once", () => {
  const stream = new MsePackets();
  assert.equal(stream.frame(frame(0n, true)), undefined);
  stream.configure(new Uint8Array([7, 8]));
  assert.equal(stream.frame(frame(1n)), undefined);
  assert.deepEqual(
    [...stream.frame(frame(2n, true))!.video],
    [7, 8, 0, 0, 1, 0x65],
  );
  assert.deepEqual([...stream.frame(frame(33335n))!.video], [0, 0, 1, 0x41]);
});

test("rotation changes configuration and requires another IDR; identical configuration is stable", () => {
  const stream = new MsePackets();
  stream.configure(new Uint8Array([7, 8]));
  stream.frame(frame(0n, true));
  assert.equal(stream.configure(new Uint8Array([7, 8])), false);
  assert.ok(stream.frame(frame(33333n)));
  assert.equal(stream.configure(new Uint8Array([7, 9])), true);
  assert.equal(stream.frame(frame(0n)), undefined);
  assert.deepEqual(
    [...stream.frame(frame(33333n, true))!.video],
    [7, 9, 0, 0, 1, 0x65],
  );
});

test("variable frame timing uses milliseconds while static gaps and nonpositive deltas stay bounded", () => {
  const stream = new MsePackets();
  stream.configure(new Uint8Array([7, 8]));
  assert.equal(stream.frame(frame(0n, true))!.duration, 33);
  assert.equal(stream.frame(frame(40000n))!.duration, 40);
  assert.equal(stream.frame(frame(10_000_000n))!.duration, 250);
  assert.equal(stream.frame(frame(10_000_000n))!.duration, 33);
  assert.equal(stream.frame(frame(1n))!.duration, 33);
  assert.equal(stream.frame(frame(2n))!.duration, 1);
});
