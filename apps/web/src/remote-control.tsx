import { lazy, Suspense, useEffect, useRef, useState } from "react";
import { Alert, Button, Input, Space } from "antd";
import { useRemoteGesture } from "./remote-gesture";

const VideoScreen = lazy(() =>
  import("./video-screen").then((m) => ({ default: m.VideoScreen })),
);

export function RemoteControl({ id, serial }: { id: string; serial: string }) {
  const [snapshotMode, setSnapshotMode] = useState(false);
  const [frame, setFrame] = useState<{
    png: string;
    width: number;
    height: number;
    captured_at: number;
  }>();
  const [token, setToken] = useState<string>();
  const tokenRef = useRef<string | undefined>(undefined);
  const [error, setError] = useState("");
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [acquiring, setAcquiring] = useState(false);
  const inputPending = useRef(false);
  const [now, setNow] = useState(Date.now());
  const active = useRef(true);
  async function call(path: string, body?: unknown, auth?: string) {
    const response = await fetch("/api/devices/" + id + "/remote/" + path, {
      method: body === undefined ? "GET" : "POST",
      headers: {
        "Content-Type": "application/json",
        ...(auth ? { "X-Control-Token": auth } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const result = await response.json();
    if (!response.ok)
      throw new Error(result.detail || result.error || "远控连接失败");
    return result;
  }
  async function action(payload: object, auth?: string) {
    const a = await call("actions", payload, auth);
    for (let i = 0; i < 200; i++) {
      await new Promise((r) => setTimeout(r, 75));
      const result = await call("actions/" + a.id);
      if (result.state === "done") return result.result;
      if (result.state === "failed")
        throw new Error(result.result?.error || "操作未执行");
    }
    throw new Error("操作确认超时，请等待节点恢复");
  }
  useEffect(() => {
    active.current = true;
    let working = false;
    let timer: ReturnType<typeof setTimeout>;
    async function refresh() {
      if (inputPending.current) {
        timer = setTimeout(() => void refresh(), 120);
        return;
      }
      if (working) return;
      working = true;
      try {
        const f = await action({ kind: "frame" });
        if (active.current) {
          setFrame(f);
          setNow(Date.now());
        }
      } catch (e) {
        if (active.current) setError(String(e));
      } finally {
        working = false;
        if (active.current)
          timer = setTimeout(() => {
            setNow(Date.now());
            if (snapshotMode) void refresh();
          }, 120);
      }
    }
    if (snapshotMode) void refresh();
    const heartbeat = setInterval(() => {
      const t = tokenRef.current;
      if (t)
        void call("lease", {}, t).catch((e) => {
          setError(String(e));
          tokenRef.current = undefined;
          setToken(undefined);
        });
    }, 10000);
    return () => {
      active.current = false;
      clearTimeout(timer);
      clearInterval(heartbeat);
    };
  }, [id, snapshotMode]);
  useEffect(
    () => () => {
      if (tokenRef.current)
        void call("lease", { close: true }, tokenRef.current).catch(() => {});
    },
    [id],
  );
  async function control() {
    setAcquiring(true);
    try {
      const r = await call("lease", {});
      if (!active.current) {
        await call("lease", { close: true }, r.token);
        return;
      }
      tokenRef.current = r.token;
      setToken(r.token);
      setError("");
    } catch (e) {
      if (active.current) setError(String(e));
    } finally {
      if (active.current) setAcquiring(false);
    }
  }
  async function release() {
    if (tokenRef.current)
      await call("lease", { close: true }, tokenRef.current);
    tokenRef.current = undefined;
    setToken(undefined);
  }
  async function input(payload: object) {
    if (!tokenRef.current || inputPending.current) return;
    setBusy(true);
    inputPending.current = true;
    setError("");
    try {
      await action(payload, tokenRef.current);
      if (snapshotMode) {
        const f = await action({ kind: "frame" });
        if (active.current) {
          setFrame(f);
          setNow(Date.now());
        }
      }
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
      inputPending.current = false;
    }
  }
  const frozen = !frame || now - frame.captured_at * 1000 > 10000;
  const snapshotGesture = useRemoteGesture<HTMLImageElement>(
    !!token && !busy && !frozen,
    () => ({ width: frame?.width ?? 0, height: frame?.height ?? 0 }),
    (gesture) => void input(gesture),
  );
  return (
    <>
      {error && <Alert type="warning" message={error} />}
      <Space wrap style={{ margin: "12px 0" }}>
        <Button
          onClick={() => void control()}
          disabled={!!token}
          loading={acquiring}
        >
          申请控制权
        </Button>
        <Button
          onClick={() => void release().catch((e) => setError(String(e)))}
          disabled={!token}
        >
          释放控制权
        </Button>
        <span>{token ? "独占控制中" : "只读观看"}</span>
        <Button onClick={() => setSnapshotMode(!snapshotMode)}>
          {snapshotMode ? "切换实时视频" : "截图兼容模式"}
        </Button>
        {snapshotMode && (
          <span>
            {frozen
              ? "画面等待 / 已冻结"
              : `${frame.width} × ${frame.height} · ${frame.width > frame.height ? "横屏" : "竖屏"} · ${Math.round(now - frame.captured_at * 1000)}ms 前`}
          </span>
        )}
      </Space>
      <div
        style={{ background: "#17202d", textAlign: "center", minHeight: 160 }}
      >
        {!snapshotMode && (
          <Suspense fallback={<p>正在加载视频组件</p>}>
            <VideoScreen
              id={id}
              enabled={!!token && !busy}
              onGesture={(p) => void input(p)}
            />
          </Suspense>
        )}
        {snapshotMode && frame && (
          <img
            src={"data:image/png;base64," + frame.png}
            alt={"设备 " + serial + " 当前画面"}
            draggable={false}
            style={{
              maxWidth: "100%",
              maxHeight: "58vh",
              touchAction: "none",
              opacity: frozen ? 0.5 : 1,
            }}
            {...snapshotGesture}
          />
        )}
      </div>
      <Space wrap style={{ margin: "12px 0" }}>
        {(
          [
            ["WAKEUP", "亮屏"],
            ["HOME", "Home"],
            ["BACK", "返回"],
            ["APP_SWITCH", "最近任务"],
            ["VOLUME_UP", "音量＋"],
            ["VOLUME_DOWN", "音量－"],
            ["SLEEP", "熄屏"],
          ] as const
        ).map(([key, label]) => (
          <Button
            key={key}
            disabled={!token || busy}
            onClick={() => void input({ kind: "key", key })}
          >
            {label}
          </Button>
        ))}
      </Space>
      <Space.Compact style={{ width: "100%" }}>
        <Input
          aria-label="远控文字"
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="输入中文、英文或符号"
          disabled={!token || busy}
        />
        <Button
          disabled={!token || busy || !text}
          onClick={() => void input({ kind: "text", text })}
        >
          发送文字
        </Button>
      </Space.Compact>
      <p>支持点击、滑动和滑动后停留。测试占用期间保持只读观看。</p>
    </>
  );
}
