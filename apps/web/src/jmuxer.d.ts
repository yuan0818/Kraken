// jmuxer 2.1.4 does not ship TypeScript declarations.
declare module "jmuxer" {
  export default class JMuxer {
    constructor(options: {
      node: HTMLVideoElement;
      mode: "video";
      flushingTime: number;
      maxDelay: number;
      clearBuffer: boolean;
      fps: number;
      onReady: () => void;
      onError: (error: { name?: string }) => void;
      onUnsupportedCodec: (codec: string) => void;
    });
    feed(data: {
      video: Uint8Array;
      duration: number;
      isLastVideoFrameComplete: boolean;
    }): void;
    destroy(): void;
  }
}
