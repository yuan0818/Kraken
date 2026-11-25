import JMuxer from "jmuxer";
import { MsePackets, type VideoPacket } from "./video-packet";

export function supportsMse() {
  return (
    typeof MediaSource !== "undefined" &&
    MediaSource.isTypeSupported('video/mp4; codecs="avc1.42E01E"')
  );
}

export class MseVideoPlayer {
  private muxer?: JMuxer;
  private packets = new MsePackets();
  private disposed = false;
  private callback = 0;
  private rendered = 0;
  private baseline = 0;
  private objectUrl = "";
  private generation = 0;
  private opened = Promise.resolve();
  private resolveOpened = () => {};
  private openTimer?: ReturnType<typeof setTimeout>;
  private video: HTMLVideoElement;
  private fail: (reason: string) => void;

  constructor(video: HTMLVideoElement, fail: (reason: string) => void) {
    this.video = video;
    this.fail = fail;
    video.addEventListener("error", this.onError);
  }

  private countFrames(generation: number) {
    if (typeof this.video.requestVideoFrameCallback !== "function") return;
    const frame = () => {
      if (this.disposed || generation !== this.generation) return;
      this.rendered++;
      this.callback = this.video.requestVideoFrameCallback(frame);
    };
    this.callback = this.video.requestVideoFrameCallback(frame);
  }

  private onError = () => {
    if (!this.disposed && this.video.error)
      this.fail(
        `MSE 播放失败：${this.video.error.message || this.video.error.code}`,
      );
  };

  get framesRendered() {
    return typeof this.video.requestVideoFrameCallback === "function"
      ? this.rendered
      : Math.max(
          0,
          (this.video.getVideoPlaybackQuality?.().totalVideoFrames ?? 0) -
            this.baseline,
        );
  }
  get width() {
    return this.video.videoWidth;
  }
  get height() {
    return this.video.videoHeight;
  }

  async write(packet: VideoPacket) {
    if (this.disposed) return;
    if (packet.type === "configuration") {
      if (this.packets.configure(packet.data)) this.restart();
    } else {
      const generation = this.generation;
      // jmuxer can drop its init segment if feed precedes MediaSource.sourceopen.
      await this.opened;
      if (this.disposed || generation !== this.generation) return;
      const frame = this.packets.frame(packet);
      if (frame) this.muxer?.feed(frame);
    }
  }

  private restart() {
    this.release();
    const generation = this.generation;
    this.opened = new Promise((resolve) => {
      this.resolveOpened = resolve;
    });
    this.openTimer = setTimeout(() => {
      if (!this.disposed && generation === this.generation)
        this.fail("MSE 初始化超时，请重试或使用截图兼容模式");
    }, 8000);
    this.rendered = 0;
    this.baseline =
      this.video.getVideoPlaybackQuality?.().totalVideoFrames ?? 0;
    this.muxer = new JMuxer({
      node: this.video,
      mode: "video",
      flushingTime: 50,
      maxDelay: 250,
      clearBuffer: true,
      fps: 30,
      onReady: () => {
        if (this.disposed || generation !== this.generation) return;
        clearTimeout(this.openTimer);
        this.countFrames(generation);
        this.resolveOpened();
        void this.video.play().catch((error: Error) => {
          if (
            !this.disposed &&
            generation === this.generation &&
            error.name !== "AbortError"
          )
            this.fail("浏览器无法自动播放视频，请重试或切换截图兼容模式");
        });
      },
      onError: (error) => {
        if (!this.disposed)
          this.fail(`MSE 缓冲失败：${error.name || "未知错误"}`);
      },
      onUnsupportedCodec: (codec) => {
        if (!this.disposed)
          this.fail(`浏览器不支持视频编码 ${codec}，请切换截图兼容模式`);
      },
    });
    this.objectUrl = this.video.src;
  }

  /** Keep both latency and retained media bounded during long remote sessions. */
  maintain() {
    const { buffered, currentTime } = this.video;
    if (buffered.length) {
      const end = buffered.end(buffered.length - 1);
      if (!this.video.seeking && end - currentTime > 0.5)
        this.video.currentTime = Math.max(
          buffered.start(buffered.length - 1),
          end - 0.1,
        );
    }
  }

  private release() {
    this.generation++;
    clearTimeout(this.openTimer);
    this.resolveOpened();
    if (this.callback) this.video.cancelVideoFrameCallback(this.callback);
    this.callback = 0;
    this.video.pause();
    this.muxer?.destroy();
    this.muxer = undefined;
    if (this.objectUrl) URL.revokeObjectURL(this.objectUrl);
    this.objectUrl = "";
    this.video.removeAttribute("src");
    this.video.load();
  }

  dispose() {
    this.disposed = true;
    this.video.removeEventListener("error", this.onError);
    this.release();
  }
}
