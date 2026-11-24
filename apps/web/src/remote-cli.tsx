import { useEffect, useRef, useState } from "react";
import { engineText, time } from "./ui";

type CommandRecord = {
  id: string;
  command: string;
  state: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  result: {
    stdout?: string;
    stderr?: string;
    exit_code?: number;
    duration_ms?: number;
    error?: string;
    truncated?: boolean;
    timed_out?: boolean;
  } | null;
};

const labels: Record<string, string> = {
  queued: "等待执行",
  executing: "执行中",
  done: "成功",
  failed: "失败",
};

export function RemoteCli({
  id,
  serial,
  enabled,
  busy,
  execute,
}: {
  id: string;
  serial: string;
  enabled: boolean;
  busy: boolean;
  execute: (payload: { kind: "shell"; command: string }) => Promise<boolean>;
}) {
  const [command, setCommand] = useState("");
  const [page, setPage] = useState(1);
  const [history, setHistory] = useState<{
    items: CommandRecord[];
    total: number;
  }>({ items: [], total: 0 });
  const [historyIndex, setHistoryIndex] = useState(-1);
  const draft = useRef("");
  const transcript = useRef<HTMLDivElement>(null);
  const input = useRef<HTMLTextAreaElement>(null);
  const pinned = useRef(true);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setLoading(true);
    const load = async () => {
      try {
        const response = await fetch(
          `/api/devices/${id}/remote/commands?page=${page}&page_size=10`,
          { signal: controller.signal },
        );
        if (!response.ok) throw new Error("命令历史读取失败");
        const value = await response.json();
        if (!controller.signal.aborted) {
          setHistory(value);
          setError("");
        }
      } catch (e) {
        if (!controller.signal.aborted) setError(String(e));
      } finally {
        if (!controller.signal.aborted) {
          setLoading(false);
          timer = setTimeout(() => void load(), 3000);
        }
      }
    };
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [id, page, refresh]);
  const transcriptVersion = history.items
    .map((item) => `${item.id}:${item.state}`)
    .join(",");
  useEffect(() => {
    if (page === 1 && pinned.current && transcript.current)
      transcript.current.scrollTop = transcript.current.scrollHeight;
  }, [transcriptVersion, page]);
  const submit = async () => {
    if (!enabled || busy || !command.trim()) return;
    const submitted = command;
    pinned.current = true;
    setPage(1);
    setHistoryIndex(-1);
    setRefresh((n) => n + 1);
    try {
      if (await execute({ kind: "shell", command: submitted }))
        setCommand((current) => (current === submitted ? "" : current));
    } finally {
      setRefresh((n) => n + 1);
      input.current?.focus();
    }
  };
  return (
    <section className="remote-terminal" aria-label="Web CLI Terminal">
      <header className="terminal-header">
        <strong>
          <span aria-hidden="true">&gt;_</span> Terminal
        </strong>
        <span>{serial} · Android Shell</span>
        <span className={enabled ? "terminal-connected" : "terminal-muted"}>
          {enabled ? "已获得控制权" : "只读"}
        </span>
      </header>
      <div className="terminal-history-nav">
        <span>命令历史 · {history.total}</span>
        <div>
          <button
            disabled={page * 10 >= history.total}
            onClick={() => setPage(page + 1)}
          >
            更早记录
          </button>
          <span>
            {page} / {Math.max(1, Math.ceil(history.total / 10))}
          </span>
          <button
            disabled={page === 1}
            onClick={() => {
              pinned.current = true;
              setPage(page - 1);
            }}
          >
            较新记录
          </button>
        </div>
      </div>
      <div
        className="terminal-transcript"
        ref={transcript}
        role="region"
        aria-label="终端输出"
        tabIndex={0}
        onScroll={(e) => {
          const el = e.currentTarget;
          pinned.current =
            el.scrollHeight - el.scrollTop - el.clientHeight < 40;
        }}
      >
        <p className="terminal-muted">
          Android Shell · 每条命令最多 15 秒 · 历史自动保存
        </p>
        {error && (
          <p role="alert" className="terminal-error">
            {error}
          </p>
        )}
        {!history.items.length && (
          <p className="terminal-muted">
            {loading ? "正在读取历史…" : "尚无命令记录，输入命令开始。"}
          </p>
        )}
        {[...history.items].reverse().map((item) => (
          <article className="terminal-entry" key={item.id}>
            <div className="terminal-entry-meta">
              <time title={item.created_at}>{time(item.created_at)}</time>
              <span
                className={
                  item.state === "failed"
                    ? "terminal-error"
                    : item.state === "done"
                      ? "terminal-connected"
                      : "terminal-muted"
                }
              >
                {labels[item.state]}
                {item.result?.exit_code !== undefined
                  ? ` · exit ${item.result.exit_code}`
                  : ""}
                {item.result?.duration_ms !== undefined
                  ? ` · ${(item.result.duration_ms / 1000).toFixed(2)}s`
                  : ""}
              </span>
              <button
                onClick={() => {
                  setCommand(item.command);
                  setHistoryIndex(-1);
                  input.current?.focus();
                }}
              >
                复用命令
              </button>
            </div>
            <pre className="terminal-command">
              <span className="terminal-prompt">$ </span>
              {item.command}
            </pre>
            {item.result?.stdout && (
              <pre aria-label="命令标准输出">{item.result.stdout}</pre>
            )}
            {item.result?.stderr && (
              <pre className="terminal-error" aria-label="命令错误输出">
                {item.result.stderr}
              </pre>
            )}
            {item.result?.error && (
              <p className="terminal-error">{engineText(item.result.error)}</p>
            )}
            {(item.state === "queued" || item.state === "executing") && (
              <p className="terminal-muted">{labels[item.state]}…</p>
            )}
          </article>
        ))}
      </div>
      <div className="terminal-input-row">
        <span className="terminal-prompt" aria-hidden="true">
          $
        </span>
        <textarea
          ref={input}
          aria-label="设备 Shell 命令"
          value={command}
          rows={2}
          maxLength={8192}
          spellCheck={false}
          autoCapitalize="off"
          autoCorrect="off"
          placeholder={enabled ? "输入命令…" : "申请控制权后可执行命令"}
          onChange={(e) => {
            setCommand(e.target.value);
            setHistoryIndex(-1);
          }}
          onKeyDown={(e) => {
            if (e.nativeEvent.isComposing) return;
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              void submit();
            }
            if (
              (historyIndex >= 0 || !command.includes("\n")) &&
              (e.key === "ArrowUp" || e.key === "ArrowDown")
            ) {
              e.preventDefault();
              if (historyIndex === -1) draft.current = command;
              const next = Math.max(
                -1,
                Math.min(
                  history.items.length - 1,
                  historyIndex + (e.key === "ArrowUp" ? 1 : -1),
                ),
              );
              setHistoryIndex(next);
              setCommand(
                next === -1 ? draft.current : history.items[next].command,
              );
            }
          }}
        />
        <button
          className="terminal-send"
          disabled={!enabled || busy || !command.trim()}
          onClick={() => void submit()}
        >
          {busy ? "执行中…" : "执行命令"}
        </button>
      </div>
      <footer className="terminal-footer">
        Enter 执行 · Shift + Enter 换行 · ↑↓ 历史{!enabled && " · 需要控制权"}
      </footer>
    </section>
  );
}
