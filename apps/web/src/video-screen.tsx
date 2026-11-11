import { useEffect, useRef, useState } from "react";
import { Alert } from "antd";
import { useRemoteGesture } from "./remote-gesture";
import { ScrcpyVideoCodecId } from "@yume-chan/scrcpy";
import { BitmapVideoFrameRenderer, WebCodecsVideoDecoder } from "@yume-chan/scrcpy-decoder-webcodecs";

export function VideoScreen({ id, enabled, onGesture }: {
  id: string; enabled: boolean;
  onGesture: (gesture: object) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const [status, setStatus] = useState("正在连接视频");
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const gesture = useRemoteGesture<HTMLCanvasElement>(enabled && ready, (element) => ({
    width: element.width, height: element.height,
  }), onGesture);
  useEffect(() => {
    if (!WebCodecsVideoDecoder.isSupported) {
      setError("当前浏览器不支持 WebCodecs，请使用 Chrome / Edge，并通过 localhost 或 HTTPS 访问");
      return;
    }
    let disposed = false;
    let socket: WebSocket | undefined;
    let timer: ReturnType<typeof setTimeout>;
    let cleanup = () => {};
    function open() {
      if (disposed) return;
      setReady(false);
      const decoder = new WebCodecsVideoDecoder({ codec: ScrcpyVideoCodecId.H264, renderer: new BitmapVideoFrameRenderer(canvas.current!) });
      const writer = decoder.writable.getWriter();
      let chain = Promise.resolve();
      let pending = 0;
      let pendingBytes = 0;
      let backend = "";
      let generationStart = 0;
      let lastSignal = performance.now();
      socket = new WebSocket(`${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/devices/${id}/video`);
      const ws = socket;
      ws.binaryType = "arraybuffer";
      const stats = setInterval(() => {
        if (performance.now() - lastSignal > 7000) {
          setError("视频通道超过 7 秒未响应，正在重连"); setReady(false); ws.close(); return;
        }
        if (decoder.framesRendered > generationStart) {
          setReady(true);
          setStatus(`${decoder.width} × ${decoder.height} · 已播放 ${decoder.framesRendered} 帧${backend === "screenrecord" ? " · 兼容编码" : " · scrcpy"}`);
          if (canvas.current) canvas.current.dataset.frames = String(decoder.framesRendered);
        }
      }, 500);
      const fail = (reason: string) => { if (!disposed) { setError(reason); setReady(false); } ws.close(); };
      ws.onmessage = (event) => {
        lastSignal = performance.now();
        if (typeof event.data === "string") {
          const state = JSON.parse(event.data);
          if (state.backend) backend = state.backend;
          if (state.state === "error") {
            generationStart = decoder.framesRendered; setReady(false); setError(state.detail || "视频连接中断");
            if (state.detail === "播放端积压，请重新连接") ws.close();
            return;
          }
          if (state.state === "starting" || state.state === "waiting" || state.state === "stopped") {
            generationStart = decoder.framesRendered;
            setReady(false); setStatus(state.detail || "正在启动视频");
          }
          return;
        }
        pendingBytes += event.data.byteLength;
        if (++pending > 600 || pendingBytes > 8_000_000) { fail("解码积压，正在重新连接"); return; }
        const data = new Uint8Array(event.data);
        const view = new DataView(data.buffer);
        if (data.length < 12 || view.getUint32(8) !== data.length - 12) { fail("视频包格式错误"); return; }
        const flags = view.getBigUint64(0);
        const packet = flags & (1n << 63n)
          ? { type: "configuration" as const, data: data.subarray(12) }
          : { type: "data" as const, data: data.subarray(12), keyframe: !!(flags & (1n << 62n)), pts: flags & ((1n << 62n) - 1n) };
        chain = chain.then(async () => {
          if (disposed || ws.readyState !== WebSocket.OPEN) return;
          await writer.write(packet);
          pending--;
          pendingBytes -= data.byteLength;
          setError("");
        }).catch((e) => fail(`视频解码失败：${String(e)}`));
      };
      cleanup = () => { clearInterval(stats); ws.onclose = null; ws.close(); decoder.dispose(); void writer.abort().catch(() => {}); };
      ws.onclose = () => {
        cleanup();
        if (!disposed) { setReady(false); setStatus("视频断开，正在重连"); timer = setTimeout(open, 2500); }
      };
      ws.onerror = () => { if (!disposed) setError("视频连接失败，正在重试"); };
    }
    open();
    return () => { disposed = true; clearTimeout(timer); cleanup(); };
  }, [id]);
  return <>
    {error && <Alert type="warning" message={error} />}
    <p aria-live="polite" style={{color: "#dbeafe", paddingTop: 8}}>{status}</p>
    <canvas ref={canvas} aria-label="设备实时视频" style={{ maxWidth: "100%", maxHeight: "58vh", touchAction: "none", opacity: ready ? 1 : 0.4 }}
      {...gesture} />
  </>;
}
