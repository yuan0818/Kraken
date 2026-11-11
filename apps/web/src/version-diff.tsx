import { useState } from "react";
import { Empty, Select } from "antd";
import type { Content, Overview } from "@kraken/api-client";
import { caseStatuses, snapshotLabel } from "./case-model";
import { shortId } from "./ui";

const fields: [keyof Content, string][] = [
  ["target", "被测对象"],
  ["app_id", "App"],
  ["module_id", "模块"],
  ["purpose", "测试目的"],
  ["preconditions", "前置条件"],
  ["steps", "步骤"],
  ["assertions", "预期结果"],
  ["android_version_ids", "Android 版本"],
  ["app_version_ids", "App 版本"],
  ["device_model_ids", "适用机型"],
  ["chip_platform_ids", "芯片平台"],
  ["level", "级别"],
  ["status", "状态"],
  ["required_tags", "设备标签"],
  ["title", "版本说明"],
];
function value(
  content: Content | null | undefined,
  key: keyof Content,
): string {
  if (!content) return "";
  if (key === "steps" && content.steps_markdown) return content.steps_markdown;
  if (key === "assertions" && content.assertions_markdown)
    return content.assertions_markdown;
  const raw = content[key];
  if (raw === undefined || raw === null) return "";
  if (key === "target") return raw === "app" ? "App" : "Android OS";
  if (key === "status") return caseStatuses[raw as keyof typeof caseStatuses];
  if (key.endsWith("_id")) return snapshotLabel(content, String(raw));
  if (Array.isArray(raw)) {
    if (key.endsWith("_ids"))
      return raw
        .map((id) => snapshotLabel(content, id))
        .sort()
        .join("\n");
    return (key === "required_tags" ? [...raw].sort() : raw).join("\n");
  }
  return String(raw);
}
export function VersionDiff({
  before,
  after,
  beforeLabel = "平台当前版本",
  afterLabel = "拟采纳内容",
}: {
  before?: Content | null;
  after?: Content | null;
  beforeLabel?: string;
  afterLabel?: string;
}) {
  const changed = fields.filter(
    ([key]) => value(before, key) !== value(after, key),
  );
  return changed.length ? (
    <div className="version-diff">
      <div className="diff-heading">
        <span>变化字段</span>
        <strong>{beforeLabel}</strong>
        <strong>{afterLabel}</strong>
      </div>
      {changed.map(([key, label]) => (
        <div className="diff-row" key={key}>
          <span>{label}</span>
          <pre>{value(before, key) || "未填写"}</pre>
          <pre>{value(after, key) || "未填写"}</pre>
        </div>
      ))}
    </div>
  ) : (
    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="内容没有差异" />
  );
}
type Version = Overview["versions"][number];
export function VersionComparison({
  current,
  versions,
  stableId,
}: {
  current: Version;
  versions: Version[];
  stableId?: string | null;
}) {
  const candidates = versions.filter((v) => v.id !== current.id);
  const [baseline, setBaseline] = useState(
    candidates.find((v) => v.id === stableId)?.id ?? candidates[0]?.id,
  );
  if (!candidates.length) return null;
  const before = candidates.find((v) => v.id === baseline) ?? candidates[0];
  return (
    <div className="version-comparison">
      <div className="panel-heading">
        <div>
          <h2>版本差异</h2>
          <p>比较所选版本与历史版本，仅展示变化字段。</p>
        </div>
        <Select
          aria-label="比较基准版本"
          value={before.id}
          onChange={setBaseline}
          options={candidates.map((v) => ({
            value: v.id,
            label: `版本 ${versions.length - versions.indexOf(v)} · ${shortId(v.id)}${v.id === stableId ? " · Stable" : ""}`,
          }))}
        />
      </div>
      <VersionDiff
        before={before.content}
        after={current.content}
        beforeLabel={`基准 · ${shortId(before.id)}`}
        afterLabel={`所选 · ${shortId(current.id)}`}
      />
    </div>
  );
}
