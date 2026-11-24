import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Input,
  Select,
  Segmented,
  Table,
  App as AntApp,
} from "antd";
import {
  api,
  type CaseSearchPage,
  type CaseListRecord,
} from "@kraken/api-client";
import { navigate, usePlatform } from "../state";
import {
  Back,
  CaseBody,
  Icon,
  NoData,
  PageHeading,
  Status,
  shortId,
  time,
} from "../ui";
import { caseStatuses, levels, snapshotLabel } from "../case-model";
import { VersionComparison } from "../version-diff";

type Filters = {
  q?: string;
  code?: string;
  purpose?: string;
  module_id?: string;
  app_id?: string;
  level?: string;
  status?: string;
  android_version_id?: string;
  app_version_id?: string;
};
const filterKeys = [
  "q",
  "code",
  "purpose",
  "module_id",
  "app_id",
  "level",
  "status",
  "android_version_id",
  "app_version_id",
] as const;

function editCaseHref(id: string, version?: string | null, back?: string) {
  const params = new URLSearchParams();
  if (version) params.set("version", version);
  if (back) params.set("back", back);
  return `#/cases/${id}/edit${params.size ? `?${params}` : ""}`;
}

export function CasesPage() {
  const { data, busy, route, setTaskCase } = usePlatform();
  const [target, setTarget] = useState(() => {
    const value = route.query.get("target");
    return value === "app" || value === "android_os" ? value : "all";
  });
  const [filters, setFilters] = useState<Filters>(() =>
    Object.fromEntries(
      filterKeys.map((key) => [key, route.query.get(key) ?? undefined]),
    ),
  );
  const [page, setPage] = useState(() =>
    Math.max(1, Number.parseInt(route.query.get("page") ?? "", 10) || 1),
  );
  const [result, setResult] = useState<CaseSearchPage>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const params = new URLSearchParams({ page: String(page), page_size: "10" });
  if (target !== "all") params.set("target", target);
  Object.entries(filters).forEach(([k, v]) => {
    if (v) params.set(k, v);
  });
  const query = params.toString();
  const backParams = new URLSearchParams(params);
  backParams.delete("page_size");
  if (page === 1) backParams.delete("page");
  const back = backParams.size ? `/cases?${backParams}` : "/cases";
  const revision = `${data?.versions.length}:${data?.cases.map((c) => c.stable_version_id).join(",")}`;
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    const timer = setTimeout(() => {
      api
        .searchCases(query, controller.signal)
        .then((value) => {
          setResult(value);
          setError("");
        })
        .catch((e) => {
          if (!controller.signal.aborted) setError(e.message);
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 220);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, revision, retry]);
  if (!data) return null;
  const catalogs = data.catalogs ?? [];
  const update = (key: keyof Filters, value?: string) => {
    setPage(1);
    setFilters((old) => ({ ...old, [key]: value }));
  };
  const names = (kind: string, parent?: string) =>
    catalogs
      .filter((c) => c.kind === kind && (!parent || c.parent_id === parent))
      .map((c) => ({
        value: c.id,
        label: c.name + (c.enabled ? "" : "（已停用）"),
      }));
  const modules = catalogs
    .filter((c) =>
      target === "android_os"
        ? c.kind === "system_module"
        : target === "app"
          ? c.kind === "app_module" &&
            (!filters.app_id || c.parent_id === filters.app_id)
          : ["system_module", "app_module"].includes(c.kind),
    )
    .map((c) => ({
      value: c.id,
      label:
        c.kind === "app_module"
          ? `${catalogs.find((a) => a.id === c.parent_id)?.name ?? "App"} / ${c.name}`
          : c.name,
    }));
  return (
    <>
      <PageHeading
        eyebrow="TEST LIBRARY"
        title="用例管理"
        description="管理结构化测试用例与适用范围，所有内容变更均保留为新版本。"
        actions={
          <>
            <Button href="#/imports">Excel 导入</Button>
            <Button href="#/catalogs" icon={<Icon name="layers" />}>
              管理基础数据
            </Button>
            <Button
              type="primary"
              icon={<Icon name="plus" />}
              href={`#/cases/new?${new URLSearchParams({ target: target === "app" ? "app" : "android_os", back })}`}
              disabled={busy}
            >
              新建用例
            </Button>
          </>
        }
      />
      <section className="panel list-panel">
        <div className="list-tabs">
          <Segmented
            aria-label="被测对象筛选"
            value={target}
            onChange={(value) => {
              setTarget(value);
              setFilters({});
              setPage(1);
            }}
            options={[
              { label: "全部用例", value: "all" },
              { label: "Android OS", value: "android_os" },
              { label: "App", value: "app" },
            ]}
          />
          <span className="muted">按最新编写版本检索 · 执行读取 Stable</span>
        </div>
        <div className="case-search-grid">
          <div className="search-field">
            <span>编号</span>
            <Input
              aria-label="检索编号"
              placeholder="输入用例编号"
              value={filters.code ?? ""}
              onChange={(e) => update("code", e.target.value)}
              allowClear
            />
          </div>
          <div className="search-field">
            <span>模块</span>
            <Select
              aria-label="检索模块"
              placeholder="全部模块"
              value={filters.module_id}
              onChange={(v) => update("module_id", v)}
              options={modules}
              allowClear
              showSearch
              optionFilterProp="label"
            />
          </div>
          <div className="search-field">
            <span>级别</span>
            <Select
              aria-label="检索级别"
              placeholder="全部级别"
              value={filters.level}
              onChange={(v) => update("level", v)}
              options={levels.map((v) => ({ value: v, label: v }))}
              allowClear
            />
          </div>
          <div className="search-field">
            <span>状态</span>
            <Select
              aria-label="检索状态"
              placeholder="全部状态"
              value={filters.status}
              onChange={(v) => update("status", v)}
              options={Object.entries(caseStatuses).map(([value, label]) => ({
                value,
                label,
              }))}
              allowClear
            />
          </div>
          <div className="search-field search-wide">
            <span>测试目的</span>
            <Input
              aria-label="检索测试目的"
              placeholder="输入测试目的关键词"
              value={filters.purpose ?? ""}
              onChange={(e) => update("purpose", e.target.value)}
              allowClear
            />
          </div>
          {target === "app" ? (
            <>
              <div className="search-field">
                <span>App</span>
                <Select
                  aria-label="检索 App"
                  placeholder="全部 App"
                  value={filters.app_id}
                  onChange={(value) => {
                    setPage(1);
                    setFilters((old) => ({
                      ...old,
                      app_id: value,
                      module_id: undefined,
                      app_version_id: undefined,
                    }));
                  }}
                  options={names("application")}
                  allowClear
                  showSearch
                  optionFilterProp="label"
                />
              </div>
              <div className="search-field">
                <span>App 版本</span>
                <Select
                  aria-label="检索 App 版本"
                  placeholder="全部 App 版本"
                  value={filters.app_version_id}
                  onChange={(v) => update("app_version_id", v)}
                  options={catalogs
                    .filter(
                      (c) =>
                        c.kind === "app_version" &&
                        (!filters.app_id || c.parent_id === filters.app_id),
                    )
                    .map((c) => ({
                      value: c.id,
                      label: `${catalogs.find((a) => a.id === c.parent_id)?.name ?? ""} / ${c.name}`,
                    }))}
                  allowClear
                  showSearch
                  optionFilterProp="label"
                />
              </div>
            </>
          ) : (
            <div className="search-field">
              <span>Android 版本</span>
              <Select
                aria-label="检索 Android 版本"
                placeholder="全部 Android 版本"
                value={filters.android_version_id}
                onChange={(v) => update("android_version_id", v)}
                options={names("android_version")}
                allowClear
                showSearch
                optionFilterProp="label"
              />
            </div>
          )}
          <div className="search-field search-reset">
            <Button
              type="text"
              onClick={() => {
                setFilters({});
                setPage(1);
              }}
            >
              重置筛选
            </Button>
          </div>
        </div>
        <div className="case-search-summary">
          <Input
            aria-label="搜索用例"
            prefix={<Icon name="search" size={16} />}
            placeholder="搜索用例名称或关键词"
            value={filters.q ?? ""}
            onChange={(e) => update("q", e.target.value)}
            allowClear
          />
          <span>
            {loading ? "正在检索…" : `共 ${result?.total ?? 0} 个用例`}
          </span>
        </div>
        {error && (
          <Alert
            type="error"
            showIcon
            message="用例检索失败"
            description={error}
            action={
              <Button size="small" onClick={() => setRetry(retry + 1)}>
                重试
              </Button>
            }
          />
        )}
        <Table<CaseListRecord>
          className="platform-table"
          rowKey="id"
          loading={loading}
          dataSource={result?.items ?? []}
          pagination={{
            current: page,
            pageSize: 10,
            total: result?.total ?? 0,
            onChange: setPage,
            hideOnSinglePage: true,
            showSizeChanger: false,
          }}
          scroll={{ x: 1100 }}
          locale={{ emptyText: <NoData text="暂无符合条件的用例" /> }}
          columns={[
            {
              title: "编号 / 用例",
              width: 220,
              render: (_, c) => (
                <a
                  className="entity-link"
                  href={`#/cases/${c.id}${c.latest_version_id ? `?version=${c.latest_version_id}` : ""}`}
                >
                  <span className="entity-title">{c.name}</span>
                  <span className="entity-sub mono">{c.code}</span>
                </a>
              ),
            },
            {
              title: "测试目的",
              width: 220,
              render: (_, c) => (
                <span className="case-purpose-cell">
                  {c.latest_content?.purpose ||
                    c.latest_content?.title ||
                    "未填写"}
                </span>
              ),
            },
            {
              title: "对象 / 模块",
              width: 155,
              render: (_, c) =>
                c.latest_content ? (
                  <>
                    <span className="object-label">
                      {c.latest_content.target === "app"
                        ? snapshotLabel(
                            c.latest_content,
                            c.latest_content.app_id,
                          )
                        : "Android OS"}
                    </span>
                    <span className="cell-caption">
                      {snapshotLabel(
                        c.latest_content,
                        c.latest_content.module_id,
                      )}
                    </span>
                  </>
                ) : (
                  "—"
                ),
            },
            {
              title: "级别",
              width: 66,
              render: (_, c) => (
                <span
                  className={`priority priority-${c.latest_content?.level ?? "P2"}`}
                >
                  {c.latest_content?.level ?? "P2"}
                </span>
              ),
            },
            {
              title: "状态",
              width: 80,
              render: (_, c) => (
                <Status
                  state={
                    c.latest_content?.status === "active"
                      ? "available"
                      : "draft"
                  }
                  label={caseStatuses[c.latest_content?.status ?? "active"]}
                />
              ),
            },
            {
              title: "Stable",
              width: 100,
              render: (_, c) =>
                c.stable_version_id ? (
                  <>
                    <span className="stable-badge">已设置</span>
                    <span className="cell-caption mono">
                      {shortId(c.stable_version_id)}
                    </span>
                  </>
                ) : (
                  <span className="muted">未设置</span>
                ),
            },
            {
              title: "更新",
              width: 110,
              render: (_, c) => (
                <span className="muted nowrap">
                  {c.updated_at ? time(c.updated_at) : "—"}
                </span>
              ),
            },
            {
              title: "操作",
              width: 150,
              fixed: "right",
              render: (_, c) => (
                <div className="table-actions">
                  <a href={`#/cases/${c.id}`}>详情</a>
                  <Button
                    type="link"
                    size="small"
                    aria-label={`编辑用例 ${c.code}`}
                    disabled={busy || loading || !!error}
                    href={editCaseHref(c.id, c.latest_version_id, back)}
                  >
                    编辑
                  </Button>
                  <Button
                    type="link"
                    size="small"
                    disabled={
                      busy ||
                      !c.latest_content ||
                      c.latest_content.status !== "active"
                    }
                    onClick={() => setTaskCase(c.id)}
                  >
                    建任务
                  </Button>
                </div>
              ),
            },
          ]}
        />
        <div className="panel-footnote">
          <Icon name="layers" size={15} />
          筛选条件组合生效。列表展示最新编写版本；Stable
          仅在明确设置后切换，已有执行快照保持不变。
        </div>
      </section>
    </>
  );
}

export function CaseDetailPage({ id }: { id: string }) {
  const { data, route, busy, mutate, setTaskCase } = usePlatform();
  const { message } = AntApp.useApp();
  if (!data) return null;
  const item = data.cases.find((c) => c.id === id);
  if (!item)
    return (
      <>
        <Back href="#/cases" label="返回用例库" />
        <NoData text="未找到该用例" />
      </>
    );
  const versions = data.versions
    .filter((v) => v.case_id === id)
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  const version =
    versions.find((v) => v.id === route.version) ??
    versions.find((v) => v.id === item.stable_version_id) ??
    versions[0];
  const promote = async () => {
    if (!version) return;
    const ok = await mutate(async () => {
      await api.setStable(id, version.id, item.stable_version_id ?? null);
      return true;
    });
    if (ok) void message.success("Stable 已切换，后续开始的任务将读取此版本");
  };
  return (
    <>
      <Back href="#/cases" label="返回用例库" />
      <PageHeading
        title={item.name}
        description={`${item.code} · ${versions.length} 个不可变版本`}
        actions={
          <>
            <Button
              aria-label="编辑用例"
              disabled={busy}
              href={editCaseHref(id, version?.id)}
            >
              编辑用例
            </Button>
            <Button
              type="primary"
              icon={<Icon name="play" />}
              disabled={
                busy || !versions[0] || versions[0].content.status !== "active"
              }
              onClick={() => setTaskCase(id)}
            >
              创建测试任务
            </Button>
          </>
        }
      />
      <div className="detail-layout">
        <section className="panel version-navigation">
          <div className="panel-heading">
            <h2>版本记录</h2>
            <span className="count-badge">{versions.length}</span>
          </div>
          <div className="version-list">
            {versions.map((v, i) => (
              <button
                key={v.id}
                className={v.id === version?.id ? "selected" : ""}
                onClick={() => navigate(`/cases/${id}?version=${v.id}`)}
              >
                <span className="version-row">
                  <strong>版本 {versions.length - i}</strong>
                  {v.id === item.stable_version_id && (
                    <span className="stable-badge">Stable</span>
                  )}
                </span>
                <span className="version-title">{v.content.title}</span>
                <small>
                  {time(v.created_at)}
                  <span className="mono">{shortId(v.id)}</span>
                </small>
              </button>
            ))}
            {!versions.length && <NoData text="尚无版本" />}
          </div>
          <p className="side-explainer">修改会创建新版本，历史内容始终保留。</p>
        </section>
        <section className="panel version-content">
          {version ? (
            <>
              <div className="panel-heading">
                <div>
                  <h2>版本内容</h2>
                  <p>
                    创建于 {time(version.created_at)} ·{" "}
                    <span className="mono">{shortId(version.id)}</span>
                  </p>
                </div>
                {version.id === item.stable_version_id ? (
                  <Status state="available" label="当前 Stable" />
                ) : (
                  <Button
                    onClick={() => void promote()}
                    disabled={busy}
                    icon={<Icon name="check" size={16} />}
                  >
                    设为 Stable
                  </Button>
                )}
              </div>
              <CaseBody content={version.content} code={item.code} />
              <VersionComparison
                key={version.id}
                current={version}
                versions={versions}
                stableId={item.stable_version_id}
              />
              <div className="panel-footnote">
                <Icon name="help" size={15} />
                本页展示所选版本。已开始的尝试始终保留自己的执行快照。
              </div>
            </>
          ) : (
            <NoData
              text="创建首个版本，定义操作步骤和断言"
              action={
                <Button type="primary" href={editCaseHref(id)}>
                  编辑用例
                </Button>
              }
            />
          )}
        </section>
      </div>
    </>
  );
}
