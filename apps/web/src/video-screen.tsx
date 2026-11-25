import { useEffect, useRef, useState } from "react";
import { Alert, Select } from "antd";
import { useRemoteGesture } from "./remote-gesture";
import { ScrcpyVideoCodecId } from "@yume-chan/scrcpy";
import {
  BitmapVideoFrameRenderer,
  WebCodecsVideoDecoder,
} from "@yume-chan/scrcpy-decoder-webcodecs";
import { MseVideoPlayer, supportsMse } from "./video-mse";
import { parseVideoPacket, type VideoPacket } from "./video-packet";

type Playback = "auto" | "webcodecs" | "mse";
type Player = {
  readonly width: number;
  readonly height: number;
  readonly framesRendered: number;
  write(packet: VideoPacket): void | Promise<void>;
  dispose(): void;
  maintain?(): void;
};

export function VideoScreen({
  id,
  enabled,
  onGesture,
}: {
  id: string;
  enabled: boolean;
  onGesture: (gesture: object) => void;
}) {
  const canvas = useRef<HTMLCanvasElement>(null);
  const video = useRef<HTMLVideoElement>(null);
  const [preference, setPreference] = useState<Playback>("auto");
  const [fallback, setFallback] = useState(false);
  const [status, setStatus] = useState("正在连接视频");
  const [error, setError] = useState("");
  const [ready, setReady] = useState(false);
  const useMse =
    preference === "mse" ||
    (preference === "auto" && (fallback || !WebCodecsVideoDecoder.isSupported));
  const canvasGesture = useRemoteGesture<HTMLCanvasElement>(
    enabled && ready,
    (element) => ({ width: element.width, height: element.height }),
    onGesture,
  );
  const videoGesture = useRemoteGesture<HTMLVideoElement>(
    enabled && ready,
    (element) => ({ width: element.videoWidth, height: element.videoHeight }),
    onGesture,
  );

  useEffect(() => {
    setReady(false);
    setError("");
    setStatus("正在连接视频");
    if (useMse ? !supportsMse() : !WebCodecsVideoDecoder.isSupported) {
      setStatus("实时视频不可用");
      setError(
        "当前浏览器不支持所选播放方式，请切换播放方式或使用截图兼容模式",
      );
      return;
    }
    let disposed = false;
    let timer: ReturnType<typeof setTimeout>;
    let cleanup = () => {};
    function open() {
      if (disposed) return;
      setReady(false);
      let closed = false;
      let player: Player | undefined;
      let chain = Promise.resolve();
      let pending = 0;
      let pendingBytes = 0;
      let backend = "";
      let epoch = 0;
      let lastSignal = performance.now();
      let firstPacketAt = 0;
      let receivedFrames = 0;
      let lastRendered = 0;
      let lastProgress = performance.now();
      const ws = new WebSocket(
        `${location.protocol === "https:" ? "wss:" : "ws:"}//${location.host}/api/devices/${id}/video`,
      );
      ws.binaryType = "arraybuffer";
      const fail = (reason: string, decoderFailure = false) => {
        if (disposed || closed) return;
        setError(reason);
        setReady(false);
        if (
          decoderFailure &&
          preference === "auto" &&
          !useMse &&
          supportsMse()
        ) {
          setFallback(true);
        }
        ws.close();
      };
      function createPlayer(): Player {
        if (useMse)
          return new MseVideoPlayer(video.current!, (reason) =>
            fail(reason, true),
          );
        const decoder = new WebCodecsVideoDecoder({
          codec: ScrcpyVideoCodecId.H264,
          renderer: new BitmapVideoFrameRenderer(canvas.current!),
        });
        const writer = decoder.writable.getWriter();
        void writer.closed.catch((error) => {
          if (player === result) fail(`视频解码失败：${String(error)}`, true);
        });
        const result: Player = {
          get width() {
            return decoder.width;
          },
          get height() {
            return decoder.height;
          },
          get framesRendered() {
            return decoder.framesRendered;
          },
          write: (packet) => writer.write(packet),
          dispose: () => {
            decoder.dispose();
            void writer.abort().catch(() => {});
          },
        };
        return result;
      }
      function resetPlayer() {
        const previous = player;
        player = undefined;
        previous?.dispose();
        firstPacketAt = 0;
        receivedFrames = 0;
        lastRendered = 0;
        lastProgress = performance.now();
        if (canvas.current) delete canvas.current.dataset.frames;
        if (video.current) delete video.current.dataset.frames;
        setReady(false);
      }
      const stats = setInterval(() => {
        if (performance.now() - lastSignal > 7000) {
          fail("视频通道超过 7 秒未响应，正在重连");
          return;
        }
        player?.maintain?.();
        const frames = player?.framesRendered ?? 0;
        if (frames !== lastRendered) {
          lastProgress = performance.now();
          receivedFrames = 0;
          lastRendered = frames;
        }
        if (
          firstPacketAt &&
          ((!frames && performance.now() - firstPacketAt > 8000) ||
            (receivedFrames > 30 && performance.now() - lastProgress > 8000))
        ) {
          fail("视频未能播放或播放积压，正在重新连接；可切换播放方式", true);
          return;
        }
        if (player && frames > 0) {
          setReady(true);
          setStatus(
            `${player.width} × ${player.height} · 已播放 ${frames} 帧 · ${useMse ? "MSE" : "WebCodecs"}${backend === "screenrecord" ? " · 兼容编码" : " · scrcpy"}`,
          );
          const element = useMse ? video.current : canvas.current;
          if (element) element.dataset.frames = String(frames);
        }
      }, 500);
      ws.onmessage = (event) => {
        if (closed || disposed) return;
        lastSignal = performance.now();
        if (typeof event.data === "string") {
          try {
            const state = JSON.parse(event.data);
            if (state.backend) backend = state.backend;
            if (
              ["error", "starting", "waiting", "stopped"].includes(state.state)
            ) {
              epoch++;
              resetPlayer();
              setStatus(state.detail || "正在启动视频");
              if (state.state === "error")
                setError(state.detail || "视频连接中断");
              if (state.detail === "播放端积压，请重新连接") ws.close();
            }
          } catch {
            fail("视频状态格式错误");
          }
          return;
        }
        let packet: VideoPacket;
        try {
          packet = parseVideoPacket(event.data);
        } catch (error) {
          fail(String(error));
          return;
        }
        const bytes = event.data.byteLength;
        pendingBytes += bytes;
        if (++pending > 600 || pendingBytes > 8_000_000) {
          fail("解码积压，正在重新连接");
          return;
        }
        const currentEpoch = epoch;
        chain = chain
          .then(async () => {
            if (
              disposed ||
              closed ||
              currentEpoch !== epoch ||
              ws.readyState !== WebSocket.OPEN
            )
              return;
            if (packet.type === "configuration") {
              resetPlayer();
              player = createPlayer();
            }
            if (!player) return; // Never decode a new stream before SPS/PPS.
            await player.write(packet);
            if (disposed || closed || currentEpoch !== epoch) return;
            if (packet.type === "data") {
              firstPacketAt ||= performance.now();
              receivedFrames++;
            }
            if (!closed && !disposed) setError("");
          })
          .catch((error) => {
            if (currentEpoch === epoch)
              fail(`视频解码失败：${String(error)}`, true);
          })
          .finally(() => {
            pending--;
            pendingBytes -= bytes;
          });
      };
      cleanup = () => {
        closed = true;
        clearInterval(stats);
        ws.onclose = null;
        ws.onmessage = null;
        ws.onerror = null;
        ws.close();
        const previous = player;
        player = undefined;
        previous?.dispose();
      };
      ws.onclose = () => {
        cleanup();
        if (!disposed) {
          setReady(false);
          setStatus("视频断开，正在重连");
          timer = setTimeout(open, 2500);
        }
      };
      ws.onerror = () => {
        if (!disposed) setError("视频连接失败，正在重试");
      };
    }
    open();
    return () => {
      disposed = true;
      clearTimeout(timer);
      cleanup();
    };
  }, [id, preference, useMse]);

  return (
    <>
      {error && <Alert type="warning" message={error} />}
      <div style={{ paddingTop: 8 }}>
        <Select<Playback>
          aria-label="视频播放方式"
          value={preference}
          onChange={(value) => {
            setFallback(false);
            setPreference(value);
          }}
          style={{ width: 180 }}
          options={[
            { value: "auto", label: "自动选择播放方式" },
            { value: "webcodecs", label: "WebCodecs（低延迟）" },
            { value: "mse", label: "MSE（兼容播放）" },
          ]}
        />
      </div>
      <p aria-live="polite" style={{ color: "#dbeafe", paddingTop: 8 }}>
        {status}
      </p>
      <canvas
        ref={canvas}
        aria-label="设备实时视频"
        hidden={useMse}
        style={{
          maxWidth: "100%",
          maxHeight: "58vh",
          touchAction: "none",
          opacity: ready ? 1 : 0.4,
        }}
        {...canvasGesture}
      />
      <video
        ref={video}
        aria-label="设备实时视频"
        hidden={!useMse}
        autoPlay
        muted
        playsInline
        disablePictureInPicture
        style={{
          maxWidth: "100%",
          maxHeight: "58vh",
          touchAction: "none",
          opacity: ready ? 1 : 0.4,
        }}
        {...videoGesture}
      />
    </>
  );
}
