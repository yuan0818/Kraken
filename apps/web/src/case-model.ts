import type { Catalog, Content, Overview } from "@kraken/api-client";
export type CatalogKind = Catalog["kind"];
export const catalogTitles: Record<CatalogKind, string> = {
  system_module: "系统模块",
  application: "App",
  app_module: "App 模块",
  app_version: "App 版本",
  android_version: "Android 版本",
  device_model: "机型",
  chip_platform: "芯片平台",
};
export const levels = ["P0", "P1", "P2", "P3"];
export const caseStatuses = { draft: "草稿", active: "启用", disabled: "停用" };
export const catalogOptions = (
  catalogs: Catalog[],
  kind: CatalogKind,
  parentId?: string,
) =>
  catalogs
    .filter((c) => c.kind === kind && (!parentId || c.parent_id === parentId))
    .map((c) => ({
      value: c.id,
      label: c.name + (c.enabled ? "" : "（已停用）"),
      disabled: !c.enabled,
    }));
export const snapshotLabel = (content: Content, id?: string | null) =>
  id ? (content.catalog_snapshot?.[id] ?? "历史数据未记录名称") : "未设置";
export function runnable(data: Overview, caseId: string) {
  const c = data.cases.find((c) => c.id === caseId);
  const v = data.versions.find((v) => v.id === c?.stable_version_id);
  return !!v && (v.content.status ?? "active") === "active";
}
