import { useEffect, useRef, useState } from "react";
import { Alert, Button, Input, Modal, Select, Table, Tag } from "antd";
import {
  api,
  type CaseListRecord,
  type CaseSearchPage,
  type TaskCase,
} from "@kraken/api-client";
import { usePlatform } from "./state";
import { snapshotLabel } from "./case-model";

export function TaskCasePicker({
  existing,
  onAdd,
  onClose,
}: {
  existing: TaskCase[];
  onAdd: (cases: TaskCase[]) => void;
  onClose: () => void;
}) {
  const { data } = usePlatform();
  const [filters, setFilters] = useState<Record<string, string>>({});
  const [page, setPage] = useState(1);
  const [result, setResult] = useState<CaseSearchPage>();
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  const [selected, setSelected] = useState<string[]>([]);
  const cache = useRef(new Map<string, CaseListRecord>());
  const query = new URLSearchParams({
    status: "active",
    page: String(page),
    page_size: "10",
    ...Object.fromEntries(Object.entries(filters).filter(([, v]) => v)),
  }).toString();
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    const timer = setTimeout(() => {
      api
        .searchCases(query, controller.signal)
        .then((value) => {
          value.items.forEach((c) => cache.current.set(c.id, c));
          setResult(value);
          setError("");
        })
        .catch((e) => {
          if (!controller.signal.aborted) setError(e.message);
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 200);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [query, retry]);
  const update = (key: string, value?: string) => {
    setPage(1);
    setFilters((old) => ({ ...old, [key]: value ?? "" }));
  };
  const catalogs = data?.catalogs ?? [];
  const options = (kind: string, parent?: string) =>
    catalogs
      .filter((c) => c.kind === kind && (!parent || c.parent_id === parent))
      .map((c) => ({ value: c.id, label: c.name }));
  const modules = catalogs
    .filter((c) =>
      filters.target === "android_os"
        ? c.kind === "system_module"
        : filters.target === "app"
          ? c.kind === "app_module" &&
            (!filters.app_id || c.parent_id === filters.app_id)
          : ["system_module", "app_module"].includes(c.kind),
    )
    .map((c) => ({
      value: c.id,
      label:
        c.kind === "app_module"
          ? `${catalogs.find((a) => a.id === c.parent_id)?.name} / ${c.name}`
          : c.name,
    }));
  return (
    <Modal
      open
      centered
      title="添加测试用例"
      width={1100}
      styles={{ body: { maxHeight: "min(68dvh, 680px)", overflowY: "auto" } }}
      onCancel={onClose}
      footer={
        <div className="task-picker-footer">
          <span>已选 {selected.length} 个用例 · 切换筛选或翻页会保留选择</span>
          <div>
            <Button onClick={onClose}>取消</Button>
            <Button
              type="primary"
              aria-label="添加所选用例"
              disabled={!selected.length || loading || !!error}
              onClick={() =>
                onAdd(
                  selected.map((id) => {
                    const c = cache.current.get(id)!;
                    return {
                      id: c.id,
                      code: c.code,
                      name: c.name,
                      position: 0,
                      latest_content: c.latest_content,
                      stable_version_id: c.stable_version_id,
                      stable_ready: c.stable_ready,
                    };
                  }),
                )
              }
            >
              添加所选 {selected.length} 个用例
            </Button>
          </div>
        </div>
      }
    >
      <p className="form-intro">
        浏览全部已启用用例，按条件筛选后勾选。加入任务仅引用用例，执行开始时读取
        Stable。
      </p>
      <div className="task-picker-filters">
        <Input
          aria-label="筛选用例编号"
          placeholder="用例编号"
          value={filters.code}
          onChange={(e) => update("code", e.target.value)}
          allowClear
        />
        <Input
          aria-label="筛选用例名称"
          placeholder="名称或关键词"
          value={filters.q}
          onChange={(e) => update("q", e.target.value)}
          allowClear
        />
        <Select
          aria-label="筛选测试对象"
          placeholder="全部对象"
          value={filters.target || undefined}
          allowClear
          options={[
            { value: "android_os", label: "Android OS" },
            { value: "app", label: "App" },
          ]}
          onChange={(v) => {
            setPage(1);
            setFilters((old) => ({
              ...old,
              target: v ?? "",
              module_id: "",
              app_id: "",
              android_version_id: "",
              app_version_id: "",
            }));
          }}
        />
        <Select
          aria-label="筛选用例模块"
          placeholder="全部模块"
          value={filters.module_id || undefined}
          onChange={(v) => update("module_id", v)}
          options={modules}
          allowClear
          showSearch
          optionFilterProp="label"
        />
        <Input
          aria-label="筛选测试目的"
          placeholder="测试目的"
          value={filters.purpose}
          onChange={(e) => update("purpose", e.target.value)}
          allowClear
        />
        <Select
          aria-label="筛选用例级别"
          placeholder="全部级别"
          value={filters.level || undefined}
          onChange={(v) => update("level", v)}
          options={["P0", "P1", "P2", "P3"].map((value) => ({
            value,
            label: value,
          }))}
          allowClear
        />
        {filters.target === "app" ? (
          <>
            <Select
              aria-label="筛选所属 App"
              placeholder="全部 App"
              value={filters.app_id || undefined}
              options={options("application")}
              allowClear
              onChange={(v) => {
                setPage(1);
                setFilters((old) => ({
                  ...old,
                  app_id: v ?? "",
                  module_id: "",
                  app_version_id: "",
                }));
              }}
            />
            <Select
              aria-label="筛选 App 版本"
              placeholder="App 版本"
              value={filters.app_version_id || undefined}
              options={options("app_version", filters.app_id)}
              allowClear
              onChange={(v) => update("app_version_id", v)}
            />
          </>
        ) : (
          <Select
            aria-label="筛选 Android 版本"
            placeholder="Android 版本"
            value={filters.android_version_id || undefined}
            options={options("android_version")}
            allowClear
            onChange={(v) => update("android_version_id", v)}
          />
        )}
        <div>
          <Tag color="green">仅已启用</Tag>
          <Button
            type="text"
            onClick={() => {
              setFilters({});
              setPage(1);
            }}
          >
            重置
          </Button>
        </div>
      </div>
      {error && (
        <Alert
          type="error"
          message={error}
          action={<Button onClick={() => setRetry((v) => v + 1)}>重试</Button>}
        />
      )}
      <Table<CaseListRecord>
        rowKey="id"
        loading={loading}
        dataSource={result?.items ?? []}
        scroll={{ x: 850 }}
        size="middle"
        pagination={{
          current: page,
          pageSize: 10,
          total: result?.total ?? 0,
          onChange: setPage,
          showSizeChanger: false,
          showTotal: (total) => `共 ${total} 个已启用用例`,
        }}
        rowSelection={{
          selectedRowKeys: selected,
          preserveSelectedRowKeys: true,
          onChange: (keys) => setSelected(keys as string[]),
          getCheckboxProps: (c) => ({
            disabled: existing.some((e) => e.id === c.id) || loading,
            "aria-label": `选择用例 ${c.code}`,
          }),
        }}
        columns={[
          {
            title: "编号 / 用例",
            width: 230,
            render: (_, c) => (
              <>
                <strong>{c.code}</strong>
                <span className="cell-caption">{c.name}</span>
              </>
            ),
          },
          {
            title: "模块",
            width: 160,
            render: (_, c) =>
              c.latest_content
                ? snapshotLabel(c.latest_content, c.latest_content.module_id)
                : "—",
          },
          {
            title: "级别",
            width: 70,
            render: (_, c) => c.latest_content?.level ?? "P2",
          },
          {
            title: "测试目的",
            render: (_, c) => (
              <span className="case-purpose-cell">
                {c.latest_content?.purpose || c.latest_content?.title}
              </span>
            ),
          },
          {
            title: "可用性",
            width: 130,
            render: (_, c) =>
              existing.some((e) => e.id === c.id) ? (
                <Tag>已在任务中</Tag>
              ) : c.stable_ready ? (
                <Tag color="green">Stable 可执行</Tag>
              ) : (
                <Tag color="orange">待设置 Stable</Tag>
              ),
          },
        ]}
      />
    </Modal>
  );
}
