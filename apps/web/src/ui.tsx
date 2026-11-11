import type { ReactNode } from "react";
import { Button, Empty } from "antd";
import type { Overview, Content } from "@kraken/api-client";
import { caseStatuses, snapshotLabel } from "./case-model";
import { MarkdownView } from "./case-markdown";

export type Attempt = Overview["attempts"][number];
export type Device = Overview["devices"][number];
export type Node = Overview["nodes"][number];
export type Case = Overview["cases"][number];
export type IconName =
  | "overview"
  | "cases"
  | "runs"
  | "devices"
  | "nodes"
  | "schedule"
  | "reports"
  | "settings"
  | "plus"
  | "search"
  | "arrow"
  | "refresh"
  | "chevron"
  | "menu"
  | "close"
  | "play"
  | "check"
  | "clock"
  | "layers"
  | "external"
  | "back"
  | "help";
const paths: Record<IconName, ReactNode> = {
  overview: (
    <>
      <rect x="3" y="3" width="7" height="7" rx="1.4" />
      <rect x="14" y="3" width="7" height="7" rx="1.4" />
      <rect x="3" y="14" width="7" height="7" rx="1.4" />
      <rect x="14" y="14" width="7" height="7" rx="1.4" />
    </>
  ),
  cases: (
    <>
      <path d="M8 3h10a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V7z" />
      <path d="M8 3v5H4m5 5h6m-6 4h6" />
    </>
  ),
  runs: (
    <>
      <rect x="3" y="4" width="18" height="16" rx="3" />
      <path d="m10 8 6 4-6 4z" />
    </>
  ),
  devices: (
    <>
      <rect x="6" y="2" width="12" height="20" rx="3" />
      <path d="M10 5h4m-3 14h2" />
    </>
  ),
  nodes: (
    <>
      <rect x="3" y="3" width="18" height="7" rx="2" />
      <rect x="3" y="14" width="18" height="7" rx="2" />
      <path d="M7 6.5h.1M7 17.5h.1M12 6.5h5M12 17.5h5" />
    </>
  ),
  schedule: (
    <>
      <rect x="3" y="5" width="18" height="16" rx="2" />
      <path d="M7 3v4m10-4v4M3 11h18m-14 4h3m4 0h3m-10 3h3" />
    </>
  ),
  reports: (
    <>
      <path d="M13 3H5v18h14V9zM13 3v6h6M8 17v-3m4 3v-5m4 5v-2" />
    </>
  ),
  settings: (
    <>
      <path d="M4 6h16M4 12h16M4 18h16" />
      <circle cx="8" cy="6" r="2" fill="currentColor" />
      <circle cx="16" cy="12" r="2" fill="currentColor" />
      <circle cx="10" cy="18" r="2" fill="currentColor" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  search: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="m16 16 4 4" />
    </>
  ),
  arrow: <path d="M5 12h14m-5-5 5 5-5 5" />,
  refresh: (
    <>
      <path d="M20 7v5h-5M4 17v-5h5" />
      <path d="M6.3 6a8 8 0 0 1 13 2M4.7 16a8 8 0 0 0 13 2" />
    </>
  ),
  chevron: <path d="m9 5 7 7-7 7" />,
  menu: <path d="M4 6h16M4 12h16M4 18h16" />,
  close: <path d="m6 6 12 12M6 18 18 6" />,
  play: <path d="m8 5 11 7-11 7z" />,
  check: <path d="m5 12 4 4L19 6" />,
  clock: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M12 7v5l3 2" />
    </>
  ),
  layers: (
    <>
      <path d="m3 7 9-4 9 4-9 4zM3 12l9 4 9-4M3 17l9 4 9-4" />
    </>
  ),
  external: <path d="M14 3h7v7m0-7L10 14M10 4H4v16h16v-6" />,
  back: <path d="M19 12H5m5-5-5 5 5 5" />,
  help: (
    <>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.5 9a2.5 2.5 0 0 1 5 0c0 2-2.5 2-2.5 4m0 4h.01" />
    </>
  ),
};
export function Icon({ name, size = 18 }: { name: IconName; size?: number }) {
  return (
    <svg
      aria-hidden="true"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {paths[name]}
    </svg>
  );
}
export const states: Record<string, string> = {
  queued: "排队中",
  preparing: "准备中",
  starting: "启动中",
  running: "执行中",
  cancel_pending: "取消中",
  completed: "已结束",
  cancelled: "已取消",
  blocked: "已阻塞",
  needs_recovery: "待恢复",
};
export const verdicts: Record<string, string> = {
  not_evaluated: "尚未评估",
  pass: "通过",
  fail: "失败",
  blocked: "阻塞",
  inconclusive: "结论不充分",
};
export const cleanupLabels: Record<string, string> = {
  pending: "待清理",
  verified: "清理已验证",
  failed: "清理失败",
};
export const evidenceLabels: Record<string, string> = {
  missing: "无证据",
  partial: "证据不完整",
  complete: "证据完整",
};
export const isActive = (a: Attempt) =>
  ["queued", "preparing", "starting", "running", "cancel_pending"].includes(
    a.state,
  );
export const online = (n?: Node) =>
  !!n?.last_seen && Date.now() - Date.parse(n.last_seen) < 15000;
export const shortId = (id: string) => id.slice(0, 8).toUpperCase();
export const time = (value?: string | null) =>
  value
    ? new Date(value).toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        hour12: false,
      })
    : "尚未连接";
export function Status({ state, label }: { state: string; label?: string }) {
  return (
    <span className={`status status-${state}`}>
      <i />
      {label ?? states[state] ?? state}
    </span>
  );
}
export function PageHeading({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <div className="page-heading">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      <div className="page-actions">{actions}</div>
    </div>
  );
}
export function Panel({
  title,
  description,
  extra,
  children,
  className = "",
}: {
  title: string;
  description?: string;
  extra?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`panel ${className}`}>
      <div className="panel-heading">
        <div>
          <h2>{title}</h2>
          {description && <p>{description}</p>}
        </div>
        {extra}
      </div>
      {children}
    </section>
  );
}
export function NoData({ text, action }: { text: string; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description={text} />
      {action}
    </div>
  );
}
export function Metric({
  title,
  value,
  meta,
  icon,
  accent = false,
}: {
  title: string;
  value: ReactNode;
  meta: ReactNode;
  icon: IconName;
  accent?: boolean;
}) {
  return (
    <div className={`metric ${accent ? "metric-accent" : ""}`}>
      <div className="metric-top">
        <span>{title}</span>
        <Icon name={icon} />
      </div>
      <div className="metric-number">{value}</div>
      <div className="metric-meta">{meta}</div>
    </div>
  );
}
export function CaseBody({
  content,
  code,
}: {
  content: Content;
  code?: string;
}) {
  const label = (id?: string | null) => snapshotLabel(content, id);
  const scope = (ids?: string[]) =>
    ids?.length ? (
      ids.map((id) => (
        <span className="neutral-tag" key={id}>
          {label(id)}
        </span>
      ))
    ) : (
      <span className="muted">不限</span>
    );
  return (
    <div className="case-body">
      <div className="content-title">
        <span className="object-label">
          {content.target === "app" ? "App" : "Android OS"}
        </span>
        <h2>{content.title}</h2>
      </div>
      <dl className="case-metadata">
        <div>
          <dt>编号</dt>
          <dd className="mono">
            {content.case_code || code || "历史快照未记录"}
          </dd>
        </div>
        <div>
          <dt>模块</dt>
          <dd>{label(content.module_id)}</dd>
        </div>
        <div>
          <dt>级别</dt>
          <dd>
            <span className={`priority priority-${content.level ?? "P2"}`}>
              {content.level ?? "P2"}
            </span>
          </dd>
        </div>
        <div>
          <dt>状态</dt>
          <dd>
            <Status
              state={
                content.status === "disabled"
                  ? "draft"
                  : content.status === "draft"
                    ? "queued"
                    : "available"
              }
              label={caseStatuses[content.status ?? "active"]}
            />
          </dd>
        </div>
        {content.target === "app" && (
          <div>
            <dt>App</dt>
            <dd>{label(content.app_id)}</dd>
          </div>
        )}
        <div>
          <dt>
            {content.target === "app" ? "适用 App 版本" : "适用 Android 版本"}
          </dt>
          <dd className="tags">
            {scope(
              content.target === "app"
                ? content.app_version_ids
                : content.android_version_ids,
            )}
          </dd>
        </div>
        <div>
          <dt>适用机型</dt>
          <dd className="tags">{scope(content.device_model_ids)}</dd>
        </div>
        {content.target === "android_os" && (
          <div>
            <dt>适用芯片平台</dt>
            <dd className="tags">{scope(content.chip_platform_ids)}</dd>
          </div>
        )}
      </dl>
      <h3>测试目的</h3>
      <p className="case-purpose">
        {content.purpose || "历史版本未填写测试目的"}
      </p>
      <h3>
        前置条件 <span>{content.preconditions?.length ?? 0}</span>
      </h3>
      {content.preconditions?.length ? (
        <ul className="preconditions-list">
          {content.preconditions.map((p, i) => (
            <li key={i}>{p}</li>
          ))}
        </ul>
      ) : (
        <p className="case-empty-field">未填写前置条件</p>
      )}
      <h3>
        步骤 <span>{content.steps.length}</span>
      </h3>
      {content.steps_markdown ? (
        <MarkdownView source={content.steps_markdown} />
      ) : (
        <ol className="steps-list">
          {content.steps.map((step, i) => (
            <li key={i}>
              <span>{String(i + 1).padStart(2, "0")}</span>
              <p>{step}</p>
            </li>
          ))}
        </ol>
      )}
      <h3>
        预期结果 <span>{content.assertions.length}</span>
      </h3>
      {content.assertions_markdown ? (
        <MarkdownView source={content.assertions_markdown} />
      ) : (
        <ul className="assertions-list">
          {content.assertions.map((a, i) => (
            <li key={i}>
              <Icon name="check" size={16} />
              <p>{a}</p>
            </li>
          ))}
        </ul>
      )}
      <h3>附加设备标签</h3>
      <div className="tags">
        {content.required_tags?.length ? (
          content.required_tags.map((t) => (
            <span key={t} className="neutral-tag">
              {t}
            </span>
          ))
        ) : (
          <span className="muted">无附加标签约束</span>
        )}
      </div>
    </div>
  );
}
export function Back({ href, label }: { href: string; label: string }) {
  return (
    <Button
      type="text"
      href={href}
      className="back-link"
      icon={<Icon name="back" size={16} />}
    >
      {label}
    </Button>
  );
}
export function groupRuns(attempts: Attempt[]) {
  const groups = new Map<string, Attempt[]>();
  for (const a of attempts)
    groups.set(a.run_id, [...(groups.get(a.run_id) ?? []), a]);
  return [...groups.values()]
    .map((items) => items.sort((a, b) => b.number - a.number))
    .sort((a, b) => Date.parse(b[0].created_at) - Date.parse(a[0].created_at));
}
export function deviceStatus(device: Device, nodes: Node[]) {
  if (device.remote_held) return { state: "running", label: "远控占用" };
  if (device.quarantined) return { state: "needs_recovery", label: "已隔离" };
  if (device.attempt_id) return { state: "running", label: "测试占用" };
  if (device.adb_state) {
    if (
      !online(nodes.find((n) => n.id === device.node_id)) ||
      !device.observed_at ||
      Date.now() - Date.parse(device.observed_at) >= 15000
    )
      return { state: "offline", label: "采集已中断" };
    if (device.adb_state === "device" && device.artemis_state === "ready")
      return { state: "available", label: "可调度" };
    if (device.adb_state === "device" && device.artemis_state === "busy")
      return { state: "running", label: "Artemis 占用" };
    if (device.adb_state === "device")
      return { state: "observed", label: "在线 · 仅监测" };
    const labels: Record<string, string> = {
      absent: "未连接",
      unauthorized: "待 USB 授权",
      offline: "ADB 离线",
      probe_error: "ADB 探测失败",
      no_permissions: "无 USB 访问权限",
      other: "非正常 ADB 模式",
    };
    return {
      state: "offline",
      label: labels[device.adb_state] ?? "设备不可用",
    };
  }
  if (device.quarantined) return { state: "needs_recovery", label: "已隔离" };
  if (device.attempt_id)
    return {
      state: "running",
      label: online(nodes.find((n) => n.id === device.node_id))
        ? "执行占用"
        : "离线 · 保留占用",
    };
  return online(nodes.find((n) => n.id === device.node_id))
    ? { state: "available", label: "可用" }
    : { state: "offline", label: "节点离线" };
}
