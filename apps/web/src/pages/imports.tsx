import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Table,
  Tag,
  Upload,
} from "antd";
import {
  api,
  type ImportBatch,
  type ImportHistory,
  type ImportPreviewRequest,
  type ImportRow,
  type ImportUpload,
} from "@kraken/api-client";
import { navigate, usePlatform } from "../state";
import { PageHeading, time } from "../ui";
import { VersionDiff } from "../version-diff";

type Mapping = ImportPreviewRequest["mapping"];
type Field = keyof Mapping;
const fields: {
  key: Field;
  label: string;
  aliases?: string[];
  required?: boolean;
}[] = [
  {
    key: "code",
    label: "编号",
    aliases: ["用例编号", "caseid", "casecode"],
    required: true,
  },
  { key: "name", label: "用例名称", aliases: ["名称", "name"] },
  { key: "target", label: "被测对象", aliases: ["类型", "target"] },
  {
    key: "module",
    label: "模块",
    aliases: ["系统模块", "app模块"],
    required: true,
  },
  { key: "app", label: "App", aliases: ["应用", "应用名称"] },
  { key: "purpose", label: "测试目的", required: true },
  { key: "preconditions", label: "前置条件" },
  {
    key: "steps",
    label: "步骤",
    aliases: ["测试步骤", "操作步骤"],
    required: true,
  },
  {
    key: "assertions",
    label: "预期结果",
    aliases: ["期望结果", "检查点"],
    required: true,
  },
  {
    key: "android_versions",
    label: "Android 版本",
    aliases: ["适用android版本"],
  },
  { key: "app_versions", label: "App 版本", aliases: ["适用app版本"] },
  { key: "device_models", label: "适用机型", aliases: ["机型"] },
  { key: "chip_platforms", label: "芯片平台", aliases: ["适用芯片平台"] },
  { key: "level", label: "级别", aliases: ["优先级"] },
  { key: "status", label: "状态" },
  { key: "required_tags", label: "设备标签", aliases: ["标签"] },
  { key: "title", label: "版本说明" },
];
const clean = (v: unknown) =>
  String(v ?? "")
    .replace(/[\s_]/g, "")
    .toLowerCase();
function autoMap(upload: ImportUpload, sheet: string, header: number): Mapping {
  const row =
    upload.sheets.find((s) => s.name === sheet)?.sample_rows[header - 1] ?? [];
  const mapping: Mapping = {};
  fields.forEach((field) => {
    const index = row.findIndex((v) =>
      [field.key, field.label, ...(field.aliases ?? [])].some(
        (name) => clean(name) === clean(v),
      ),
    );
    if (index >= 0) mapping[field.key] = index;
  });
  return mapping;
}
const actions: Record<ImportRow["action"], { label: string; color: string }> = {
  new: { label: "新增用例", color: "green" },
  update: { label: "新增版本", color: "blue" },
  unchanged: { label: "无变化", color: "default" },
  link_required: { label: "待关联确认", color: "orange" },
  conflict: { label: "来源与平台冲突", color: "orange" },
  invalid: { label: "校验失败", color: "red" },
};
const needsConfirmation = (row: ImportRow) =>
  ["conflict", "link_required"].includes(row.action);

export function ImportsPage() {
  const { route, busy, refresh } = usePlatform();
  const [upload, setUpload] = useState<ImportUpload>();
  const [source, setSource] = useState("");
  const [sheet, setSheet] = useState("");
  const [header, setHeader] = useState(1);
  const [target, setTarget] = useState<"app" | "android_os">("android_os");
  const [mapping, setMapping] = useState<Mapping>({});
  const [batch, setBatch] = useState<ImportBatch>();
  const [history, setHistory] = useState<ImportHistory[]>([]);
  const [selected, setSelected] = useState<number[]>([]);
  const [confirmed, setConfirmed] = useState<number[]>([]);
  const [working, setWorking] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [historyError, setHistoryError] = useState("");
  const [revision, setRevision] = useState(0);
  const request = useRef<{ signature: string; id: string } | null>(null);
  const disabled = working || busy || loading;
  useEffect(() => {
    if (batch && !loading)
      document
        .querySelector(".import-preview")
        ?.scrollIntoView({ block: "start" });
  }, [batch?.id, loading]);
  useEffect(() => {
    let active = true;
    api
      .importHistory()
      .then((value) => {
        if (active) {
          setHistory(value);
          setHistoryError("");
        }
      })
      .catch((e) => {
        if (active) setHistoryError(e.message);
      });
    return () => {
      active = false;
    };
  }, [revision]);
  useEffect(() => {
    if (!route.id) return;
    let active = true;
    setLoading(true);
    setBatch(undefined);
    setError("");
    const load = async () => {
      try {
        const value = await api.importBatch(route.id!);
        const config = value.configuration as unknown as ImportPreviewRequest;
        const file = await api.importUpload(config.upload_id);
        if (!active) return;
        setBatch(value);
        setUpload(file);
        setSource(config.source_key);
        setSheet(config.sheet);
        setHeader(config.header_row ?? 1);
        setMapping(config.mapping);
        setTarget(config.default_target ?? "android_os");
        setSelected(
          value.preview
            .filter((r) => r.action !== "invalid" && !needsConfirmation(r))
            .map((r) => r.row_number),
        );
        setConfirmed([]);
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : "读取失败");
      } finally {
        if (active) setLoading(false);
      }
    };
    void load();
    return () => {
      active = false;
    };
  }, [route.id]);
  const receiveFile = async (file: File) => {
    setWorking(true);
    setError("");
    try {
      if (!/\.xlsx$/i.test(file.name) || file.size > 10 * 1024 * 1024)
        throw new Error("请选择不超过 10 MB 的 .xlsx 文件");
      const result = await api.uploadExcel(file);
      const name = result.sheets[0].name;
      setUpload(result);
      setSheet(name);
      setHeader(1);
      setMapping(autoMap(result, name, 1));
      setSource((previous) => previous || file.name.replace(/\.xlsx$/i, ""));
      setBatch(undefined);
      setSelected([]);
      setConfirmed([]);
      request.current = null;
      navigate("/imports");
    } catch (e) {
      setError(e instanceof Error ? e.message : "上传失败");
    } finally {
      setWorking(false);
    }
    return false;
  };
  const preview = async () => {
    if (!upload) return;
    setWorking(true);
    setError("");
    try {
      const config = {
        upload_id: upload.id,
        source_key: source.trim(),
        sheet,
        header_row: header,
        mapping,
        default_target: target,
      };
      const signature = JSON.stringify(config);
      if (request.current?.signature !== signature)
        request.current = { signature, id: crypto.randomUUID() };
      const value = await api.previewImport({
        ...config,
        request_id: request.current.id,
      });
      setBatch(value);
      setConfirmed([]);
      setSelected(
        value.preview
          .filter((r) => r.action !== "invalid" && !needsConfirmation(r))
          .map((r) => r.row_number),
      );
      setRevision((v) => v + 1);
      navigate(`/imports/${value.id}`);
      // A deliberate new preview must re-read platform changes, even for the same file.
      request.current = null;
    } catch (e) {
      setError(e instanceof Error ? e.message : "预览失败");
    } finally {
      setWorking(false);
    }
  };
  const accept = async () => {
    if (!batch) return;
    setWorking(true);
    setError("");
    try {
      const value = await api.acceptImport(
        batch.id,
        selected,
        confirmed.filter((n) => selected.includes(n)),
      );
      setBatch(value);
      setRevision((v) => v + 1);
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "采纳失败");
    } finally {
      setWorking(false);
    }
  };
  const currentSheet = upload?.sheets.find((s) => s.name === sheet);
  const headers = currentSheet?.sample_rows[header - 1] ?? [];
  const width = Math.max(
    headers.length,
    ...(currentSheet?.sample_rows.map((r) => r.length) ?? [0]),
  );
  const columns = Array.from({ length: width }, (_, index) => ({
    value: index,
    label: `第 ${index + 1} 列 · ${String(headers[index] ?? "无标题")}`,
  }));
  const missing = fields.filter(
    (f) => f.required && mapping[f.key] === undefined,
  );
  const waiting =
    batch?.preview.filter(
      (r) =>
        selected.includes(r.row_number) &&
        needsConfirmation(r) &&
        !confirmed.includes(r.row_number),
    ).length ?? 0;
  const resultRows = batch?.result?.rows as
    | {
        row_number: number;
        code: string;
        case_id: string;
        version_id: string;
        outcome: string;
      }[]
    | undefined;
  const configChanged =
    !!batch &&
    (batch.configuration.upload_id !== upload?.id ||
      batch.configuration.source_key !== source.trim() ||
      batch.configuration.sheet !== sheet ||
      batch.configuration.header_row !== header ||
      batch.configuration.default_target !== target ||
      JSON.stringify(
        Object.entries(batch.configuration.mapping as Mapping).sort(),
      ) !== JSON.stringify(Object.entries(mapping).sort()));
  return (
    <>
      <PageHeading
        eyebrow="CASE IMPORTS"
        title="导入中心"
        description="上传 Excel，检查字段与差异，再选择采纳。每次采纳保留来源记录和历史版本。"
        actions={<Button href="#/cases">返回用例库</Button>}
      />
      {error && (
        <Alert
          className="connection-alert"
          type="error"
          showIcon
          message="操作未完成"
          description={error}
        />
      )}
      <section className="panel import-setup">
        <div className="panel-heading">
          <div>
            <h2>1. 上传与字段映射</h2>
            <p>.xlsx · 最多 10 MB、50 列、单批 2000 行</p>
          </div>
          <Upload
            accept=".xlsx"
            beforeUpload={receiveFile}
            showUploadList={false}
            disabled={disabled}
          >
            <Button loading={working && !upload} disabled={disabled}>
              {upload ? "更换文件" : "上传 Excel"}
            </Button>
          </Upload>
        </div>
        {!upload ? (
          <div className="import-intro">
            <strong>从现有用例表开始</strong>
            <p>
              至少包含编号、模块、测试目的、步骤、预期结果。每行对应一个用例，步骤和预期结果在单元格中换行。
            </p>
            <p>
              模块、App、版本、机型和芯片需先在{" "}
              <a href="#/catalogs">基础数据</a>{" "}
              中维护，导入时按名称匹配。编号请使用文本格式。
            </p>
          </div>
        ) : (
          <>
            <div className="import-file">
              <strong>{upload.filename}</strong>
              <a href={`/api/imports/uploads/${upload.id}/file`} download>
                下载原文件
              </a>
              <span className="muted">共 {upload.sheets.length} 个工作表</span>
            </div>
            <Form layout="vertical" className="import-config">
              <Form.Item
                label="固定来源名称"
                required
                extra="同一来源后续导入保持此名称及工作表名不变，编号用于识别用例。"
              >
                <Input
                  aria-label="固定来源名称"
                  value={source}
                  maxLength={160}
                  disabled={disabled}
                  onChange={(e) => setSource(e.target.value)}
                />
              </Form.Item>
              <Form.Item label="工作表">
                <Select
                  aria-label="工作表"
                  value={sheet}
                  disabled={disabled}
                  options={upload.sheets.map((s) => ({
                    value: s.name,
                    label: `${s.name} · ${s.row_count} 行`,
                  }))}
                  onChange={(name) => {
                    setSheet(name);
                    setMapping(autoMap(upload, name, header));
                  }}
                />
              </Form.Item>
              <Form.Item label="表头所在行">
                <InputNumber
                  aria-label="表头所在行"
                  min={1}
                  max={20}
                  value={header}
                  disabled={disabled}
                  onChange={(v) => {
                    if (v) {
                      setHeader(v);
                      setMapping(autoMap(upload, sheet, v));
                    }
                  }}
                />
              </Form.Item>
              <Form.Item label="未映射类型时使用">
                <Select
                  aria-label="默认被测对象"
                  value={target}
                  disabled={disabled}
                  onChange={setTarget}
                  options={[
                    { value: "android_os", label: "Android OS" },
                    { value: "app", label: "App" },
                  ]}
                />
              </Form.Item>
            </Form>
            <details className="import-sample">
              <summary>查看前 20 行原始内容</summary>
              <Table
                size="small"
                rowKey="row"
                pagination={false}
                scroll={{ x: Math.max(700, width * 160) }}
                dataSource={(currentSheet?.sample_rows ?? []).map(
                  (cells, i) => ({ row: i + 1, cells }),
                )}
                columns={[
                  { title: "行", dataIndex: "row", width: 55, fixed: "left" },
                  ...columns.map((c) => ({
                    title: `第 ${c.value + 1} 列`,
                    width: 160,
                    render: (_: unknown, record: { cells: unknown[] }) => (
                      <span className="source-cell">
                        {String(record.cells[c.value] ?? "")}
                      </span>
                    ),
                  })),
                ]}
              />
            </details>
            <Form layout="vertical" className="import-mapping">
              {fields.map((f) => (
                <Form.Item key={f.key} label={f.label} required={f.required}>
                  <Select
                    aria-label={`映射${f.label}`}
                    value={mapping[f.key]}
                    disabled={disabled}
                    allowClear
                    placeholder={f.required ? "请选择来源列" : "不导入此字段"}
                    options={columns}
                    onChange={(v) =>
                      setMapping((old) => {
                        const next = { ...old };
                        if (v === undefined) delete next[f.key];
                        else next[f.key] = v;
                        return next;
                      })
                    }
                  />
                </Form.Item>
              ))}
            </Form>
            <div className="import-guidance">
              未映射的可选字段保留平台已有内容；已映射的空单元格会清空该字段。多选名称用换行或分号分隔。App
              使用自身版本，OS 使用 Android
              版本与芯片平台。公式、合并单元格及附件需先整理为文本。
            </div>
            <div className="import-toolbar">
              <span className="muted">
                {missing.length
                  ? `待映射：${missing.map((f) => f.label).join("、")}`
                  : "字段已映射，请生成预览核对"}
              </span>
              <Button
                type="primary"
                disabled={disabled || !!missing.length || !source.trim()}
                loading={working}
                onClick={() => void preview()}
              >
                {batch ? "重新生成预览" : "生成导入预览"}
              </Button>
            </div>
          </>
        )}
      </section>
      {batch && (
        <section className="panel import-preview">
          <div className="panel-heading">
            <div>
              <h2>2. 核对与采纳</h2>
              <p>
                此预览来源：{String(batch.source_identity.source_key)} /{" "}
                {String(batch.source_identity.sheet)} · {time(batch.created_at)}
              </p>
            </div>
            <Tag color={batch.state === "accepted" ? "green" : "blue"}>
              {batch.state === "accepted" ? "已采纳" : "待采纳"}
            </Tag>
          </div>
          <div className="import-counts">
            {Object.entries(actions).map(([key, action]) => {
              const count = batch.preview.filter(
                (r) => r.action === key,
              ).length;
              return (
                count > 0 && (
                  <Tag key={key} color={action.color}>
                    {action.label} {count}
                  </Tag>
                )
              );
            })}
          </div>
          {configChanged && (
            <Alert
              type="warning"
              showIcon
              message="文件或映射配置已修改，请重新生成预览后再采纳。"
            />
          )}
          {batch.state === "accepted" && (
            <Alert
              type="success"
              showIcon
              message={`已处理 ${batch.result?.selected ?? 0} 行，未选择 ${batch.result?.not_selected ?? 0} 行。Stable 保持不变。`}
              description="本批次已完成。如需处理其余行，请重新生成预览。"
            />
          )}
          <Table<ImportRow>
            className="platform-table"
            rowKey="row_number"
            dataSource={batch.preview}
            loading={loading}
            scroll={{ x: 900 }}
            pagination={{
              pageSize: 10,
              hideOnSinglePage: true,
              showSizeChanger: false,
            }}
            rowSelection={
              batch.state === "accepted"
                ? undefined
                : {
                    selectedRowKeys: selected,
                    columnWidth: 44,
                    onChange: (keys) => setSelected(keys as number[]),
                    getCheckboxProps: (r) => ({
                      disabled: disabled || r.action === "invalid",
                      "aria-label": `选择第 ${r.row_number} 行`,
                    }),
                  }
            }
            expandable={{
              columnWidth: 44,
              expandedRowRender: (row) => (
                <div className="import-row-detail">
                  {!!row.errors?.length && (
                    <Alert type="error" message={row.errors?.join("；")} />
                  )}
                  {row.content && (
                    <VersionDiff before={row.before} after={row.content} />
                  )}
                  <details>
                    <summary>来源原文 · 第 {row.row_number} 行</summary>
                    <dl className="import-source">
                      {Object.entries(row.raw ?? {}).map(([key, text]) => (
                        <div key={key}>
                          <dt>
                            {fields.find((f) => f.key === key)?.label ?? key}
                          </dt>
                          <dd>{String(text ?? "空单元格")}</dd>
                        </div>
                      ))}
                    </dl>
                  </details>
                </div>
              ),
            }}
            columns={[
              { title: "行", dataIndex: "row_number", width: 55 },
              {
                title: "编号 / 名称",
                width: 200,
                render: (_, r) => (
                  <>
                    <strong className="mono">{r.code || "缺少编号"}</strong>
                    <span className="cell-caption">{r.name || "—"}</span>
                  </>
                ),
              },
              {
                title: "处理方式",
                width: 155,
                render: (_, r) => (
                  <Tag color={actions[r.action].color}>
                    {actions[r.action].label}
                  </Tag>
                ),
              },
              {
                title: "检查结果",
                render: (_, r) => (
                  <Space direction="vertical">
                    <span>
                      {[...(r.errors ?? []), ...(r.notes ?? [])].join("；") ||
                        `${r.changed_fields?.length ?? 0} 个字段变化`}
                    </span>
                    {batch.state !== "accepted" && needsConfirmation(r) && (
                      <Checkbox
                        disabled={disabled}
                        checked={confirmed.includes(r.row_number)}
                        onChange={(e) =>
                          setConfirmed((old) =>
                            e.target.checked
                              ? [...old, r.row_number]
                              : old.filter((n) => n !== r.row_number),
                          )
                        }
                      >
                        确认第 {r.row_number} 行
                        {r.action === "link_required"
                          ? "关联已有用例"
                          : "采纳来源修改"}
                      </Checkbox>
                    )}
                    {resultRows?.find((v) => v.row_number === r.row_number) &&
                      (() => {
                        const result = resultRows.find(
                          (v) => v.row_number === r.row_number,
                        )!;
                        return (
                          <a
                            href={`#/cases/${result.case_id}?version=${result.version_id}`}
                          >
                            查看
                            {result.outcome === "unchanged" ? "已有" : "采纳"}
                            版本
                          </a>
                        );
                      })()}
                  </Space>
                ),
              },
            ]}
          />
          {batch.state !== "accepted" && (
            <div className="import-toolbar">
              <span>
                已选 {selected.length} 行
                {waiting ? ` · 其中 ${waiting} 行需逐行确认` : ""} ·
                采纳不会修改 Stable
              </span>
              <Button
                type="primary"
                aria-label="采纳所选行"
                disabled={
                  disabled || configChanged || !selected.length || !!waiting
                }
                loading={working}
                onClick={() => void accept()}
              >
                采纳所选 {selected.length} 行
              </Button>
            </div>
          )}
        </section>
      )}
      <section className="panel import-history">
        <div className="panel-heading">
          <div>
            <h2>导入记录</h2>
            <p>最近 100 个批次，可返回查看预览、错误、来源与采纳结果。</p>
          </div>
          <Button disabled={disabled} onClick={() => setRevision((v) => v + 1)}>
            刷新记录
          </Button>
        </div>
        {historyError && <Alert type="error" message={historyError} />}
        <Table
          rowKey="id"
          dataSource={history}
          size="middle"
          pagination={{
            pageSize: 5,
            hideOnSinglePage: true,
            showSizeChanger: false,
          }}
          columns={[
            {
              title: "来源",
              render: (_, r) => (
                <a href={`#/imports/${r.id}`}>
                  {String(r.source_identity.source_key ?? "历史来源")}
                </a>
              ),
            },
            {
              title: "文件 / 工作表",
              render: (_, r) =>
                `${r.source_identity.filename ?? "—"} / ${r.source_identity.sheet ?? "—"}`,
            },
            {
              title: "状态",
              render: (_, r) =>
                r.state === "accepted"
                  ? `已采纳 ${r.result?.selected ?? 0} 行`
                  : "待采纳",
            },
            { title: "创建时间", render: (_, r) => time(r.created_at) },
          ]}
        />
      </section>
    </>
  );
}
