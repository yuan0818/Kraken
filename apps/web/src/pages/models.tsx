import { useEffect, useState } from "react";
import {
  Alert,
  App as AntApp,
  Button,
  Form,
  Input,
  InputNumber,
  Select,
  Space,
  Table,
  Tag,
} from "antd";
import {
  api,
  type ModelRecord,
  type ModelCreate,
  type ModelVersion,
} from "@kraken/api-client";
import { navigate, usePlatform } from "../state";
import { Back, Icon, NoData, PageHeading, time } from "../ui";

const effortNames: Record<string, string> = {
  default: "服务默认",
  none: "关闭",
  minimal: "最小",
  low: "低",
  medium: "中",
  high: "高",
  xhigh: "极高",
  max: "最大",
};

export function ModelsPage() {
  const { busy } = usePlatform();
  const { message } = AntApp.useApp();
  const [models, setModels] = useState<ModelRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [search, setSearch] = useState("");
  const [schema, setSchema] = useState<string>();
  const [checking, setChecking] = useState<string>();
  const [changing, setChanging] = useState<string>();
  const load = async () => {
    try {
      setModels(await api.models());
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "读取失败");
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    void load();
  }, []);
  const check = async (model: ModelRecord) => {
    setChecking(model.id);
    setError("");
    try {
      const result = await api.checkModel(model.id, model.revision);
      await load();
      if (result.status === "passed")
        void message.success("控制面文本连接检测通过，节点与视觉能力尚未验证");
      else void message.warning(result.detail);
    } catch (e) {
      setError(e instanceof Error ? e.message : "检测失败");
    } finally {
      setChecking(undefined);
    }
  };
  const toggle = async (model: ModelRecord) => {
    setChanging(model.id);
    setError("");
    try {
      await api.modelAvailability(model.id, model.revision, !model.enabled);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "操作失败");
    } finally {
      setChanging(undefined);
    }
  };
  const filtered = models.filter(
    (m) =>
      (!schema || m.api_schema === schema) &&
      `${m.name} ${m.model_id} ${m.provider}`
        .toLowerCase()
        .includes(search.toLowerCase()),
  );
  return (
    <>
      <PageHeading
        eyebrow="MODEL CONFIGURATIONS"
        title="模型配置"
        description="维护内网或云端模型，任务触发时固定配置版本与凭据。"
        actions={
          <Button
            type="primary"
            icon={<Icon name="plus" />}
            disabled={busy}
            href="#/models/new"
          >
            新增模型
          </Button>
        }
      />
      <Alert
        className="connection-alert"
        type="info"
        showIcon
        message="检测范围：控制面文本连接"
        description="检测会向所配置的服务发送一条固定文本请求，使用已保存的思考参数。通过不代表 Linux 节点可达、截图识别可用或设备测试通过。在真机任务中选择模型后，Artemis 会使用批次触发时固定的版本。模拟批次只记录所选配置。"
      />
      {error && (
        <Alert
          className="connection-alert"
          type="error"
          message={error}
          action={<Button onClick={() => void load()}>重新加载</Button>}
        />
      )}
      <section className="panel list-panel">
        <div className="filter-bar">
          <Input
            className="search-input"
            aria-label="搜索模型"
            placeholder="名称、模型 ID 或供应商"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            allowClear
            prefix={<Icon name="search" size={17} />}
          />
          <Select
            aria-label="筛选模型协议"
            placeholder="全部协议"
            value={schema}
            onChange={setSchema}
            allowClear
            options={[
              { value: "openai", label: "OpenAI" },
              { value: "anthropic", label: "Anthropic" },
            ]}
          />
          <span className="filter-summary">共 {filtered.length} 个模型</span>
        </div>
        <Table<ModelRecord>
          rowKey="id"
          className="platform-table"
          loading={loading}
          dataSource={filtered}
          scroll={{ x: 1150 }}
          pagination={{ pageSize: 10, hideOnSinglePage: true }}
          locale={{ emptyText: "暂无模型配置，点击新增模型开始配置" }}
          columns={[
            {
              title: "模型 / ID",
              width: 220,
              render: (_, m) => (
                <div>
                  <strong>{m.name}</strong>
                  <span className="cell-caption mono break-all">
                    {m.model_id}
                  </span>
                  <Tag color={m.enabled ? "green" : "default"}>
                    {m.enabled ? "启用" : "停用"}
                  </Tag>
                  <span className="muted">v{m.revision}</span>
                </div>
              ),
            },
            {
              title: "接口 / 供应商",
              width: 180,
              render: (_, m) => (
                <>
                  <Tag>
                    {m.api_schema === "openai" ? "OpenAI" : "Anthropic"}
                  </Tag>
                  <span className="cell-caption">{m.provider}</span>
                  <span className="cell-caption break-all">{m.url}</span>
                </>
              ),
            },
            {
              title: "上下文 / 思考",
              width: 140,
              render: (_, m) => (
                <>
                  {m.context_window.toLocaleString()} tokens
                  <span className="cell-caption">
                    {effortNames[m.thinking_effort ?? "default"]}
                  </span>
                </>
              ),
            },
            {
              title: "Key",
              width: 100,
              render: (_, m) => <Tag>{m.has_key ? "已设置" : "未设置"}</Tag>,
            },
            {
              title: "控制面检测",
              width: 250,
              render: (_, m) =>
                m.last_check ? (
                  <>
                    <Tag
                      color={
                        m.last_check.status === "passed" ? "green" : "orange"
                      }
                    >
                      {m.last_check.status === "passed"
                        ? "文本连接通过"
                        : "检测失败"}
                    </Tag>
                    <span className="cell-caption">
                      {time(m.last_check.checked_at)} ·{" "}
                      {m.last_check.latency_ms} ms
                    </span>
                    <span className="cell-caption">{m.last_check.detail}</span>
                  </>
                ) : (
                  <span className="muted">当前版本未检测</span>
                ),
            },
            {
              title: "操作",
              width: 230,
              fixed: "right",
              render: (_, m) => (
                <Space size={0} wrap>
                  <Button
                    type="link"
                    disabled={busy}
                    href={`#/models/${m.id}/edit`}
                  >
                    编辑
                  </Button>
                  <Button
                    type="link"
                    aria-label="检测连接"
                    aria-busy={checking === m.id}
                    loading={checking === m.id}
                    disabled={busy || !m.enabled || !!checking}
                    onClick={() => void check(m)}
                  >
                    检测连接
                  </Button>
                  <Button type="link" href={`#/models/${m.id}/history`}>
                    历史版本
                  </Button>
                  <Button
                    type="link"
                    disabled={busy || !!changing}
                    loading={changing === m.id}
                    onClick={() => void toggle(m)}
                  >
                    {m.enabled ? "停用" : "启用"}
                  </Button>
                </Space>
              ),
            },
          ]}
        />
        <div className="panel-footnote">
          Key 加密保存且不会回显。停用阻止新批次使用，已有批次的模型与 Key
          版本保持不变。
        </div>
      </section>
    </>
  );
}

export function ModelEditorPage({ id }: { id?: string }) {
  const [model, setModel] = useState<ModelRecord>();
  const [loading, setLoading] = useState(!!id);
  const [error, setError] = useState("");
  useEffect(() => {
    if (!id) return;
    let active = true;
    api
      .models()
      .then((models) => {
        if (active) setModel(models.find((model) => model.id === id));
      })
      .catch((e) => {
        if (active) setError(e instanceof Error ? e.message : "读取模型失败");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [id]);
  return (
    <>
      <Back href="#/models" label="返回模型配置" />
      <PageHeading
        title={id ? "编辑模型" : "新增模型"}
        description={
          model
            ? `${model.name} · v${model.revision}`
            : "配置模型接口、凭据和推理参数。"
        }
      />
      {loading ? (
        <NoData text="正在读取模型…" />
      ) : error ? (
        <Alert type="error" message={error} />
      ) : id && !model ? (
        <NoData text="未找到该模型" />
      ) : (
        <ModelEditor model={model} />
      )}
    </>
  );
}

function ModelEditor({ model: editExisting }: { model?: ModelRecord }) {
  const { busy } = usePlatform();
  const { message } = AntApp.useApp();
  const [saving, setSaving] = useState(false);
  const [formError, setFormError] = useState("");
  const [clearKey, setClearKey] = useState(false);
  const [form] = Form.useForm<ModelCreate>();
  const formSchema =
    Form.useWatch("api_schema", form) ?? editExisting?.api_schema ?? "openai";
  const initialValues = editExisting
    ? {
        model_id: editExisting.model_id,
        name: editExisting.name,
        api_schema: editExisting.api_schema,
        url: editExisting.url,
        provider: editExisting.provider,
        context_window: editExisting.context_window,
        thinking_effort: editExisting.thinking_effort,
      }
    : {
        api_schema: "openai",
        context_window: 32768,
        thinking_effort: "default",
      };
  const save = async (values: ModelCreate) => {
    const submittedFrom = window.location.hash;
    setSaving(true);
    setFormError("");
    try {
      if (editExisting)
        await api.updateModel(editExisting.id, {
          ...values,
          expected_revision: editExisting.revision,
          api_key: clearKey ? "" : values.api_key || null,
        });
      else await api.createModel({ ...values, api_key: values.api_key ?? "" });
      void message.success("模型配置已保存；已有批次继续使用原版本");
      if (window.location.hash === submittedFrom) navigate("/models");
    } catch (e) {
      setFormError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setSaving(false);
    }
  };
  return (
    <section className="panel editor-page">
      {formError && (
        <Alert className="connection-alert" type="error" message={formError} />
      )}
      <Form
        form={form}
        initialValues={initialValues}
        scrollToFirstError
        name="model-editor"
        layout="vertical"
        onFinish={save}
        disabled={saving}
      >
        <div className="model-form-grid">
          <Form.Item
            label="ID"
            name="model_id"
            rules={[
              { required: true, whitespace: true, message: "请输入模型 ID" },
            ]}
            extra="请求中的 model 标识，须与服务提供的名称一致。"
          >
            <Input maxLength={200} placeholder="例如：内网服务提供的模型标识" />
          </Form.Item>
          <Form.Item
            label="名称"
            name="name"
            rules={[
              { required: true, whitespace: true, message: "请输入显示名称" },
            ]}
          >
            <Input maxLength={200} placeholder="平台内显示名称" />
          </Form.Item>
          <Form.Item
            label="API schema"
            name="api_schema"
            rules={[{ required: true }]}
          >
            <Select
              options={[
                { value: "openai", label: "OpenAI" },
                { value: "anthropic", label: "Anthropic" },
              ]}
              onChange={() =>
                form.setFieldsValue({ thinking_effort: "default" })
              }
            />
          </Form.Item>
          <Form.Item
            label="供应商"
            name="provider"
            rules={[
              { required: true, whitespace: true, message: "请输入供应商" },
            ]}
          >
            <Input
              maxLength={200}
              placeholder="例如：内网部署 / OpenAI / Anthropic"
            />
          </Form.Item>
          <Form.Item
            className="model-field-wide"
            label="URL"
            name="url"
            rules={[
              {
                required: true,
                whitespace: true,
                message: "请输入 API 基础地址",
              },
            ]}
            extra="填写 API 基础地址；OpenAI 通常含 /v1，不含 /chat/completions、查询参数或凭据。"
          >
            <Input maxLength={2000} placeholder="https://service.example/v1" />
          </Form.Item>
          <Form.Item
            className="model-field-wide"
            label="Key"
            name="api_key"
            extra={
              editExisting?.has_key
                ? clearKey
                  ? "本次保存将清除 Key；历史版本仍保留。"
                  : "已设置；留空保留原 Key，填写则替换。"
                : "无鉴权的内网服务可留空。"
            }
          >
            <Input.Password
              maxLength={8192}
              autoComplete="new-password"
              disabled={saving || clearKey}
              placeholder={
                editExisting?.has_key
                  ? "已保存，无法回显"
                  : "输入 API Key（可选）"
              }
            />
          </Form.Item>
          {editExisting?.has_key && (
            <div className="model-key-action">
              <Button
                type="link"
                disabled={saving}
                onClick={() => {
                  setClearKey(!clearKey);
                  form.setFieldsValue({ api_key: "" });
                }}
              >
                {clearKey ? "保留原 Key" : "清除已保存的 Key"}
              </Button>
            </div>
          )}
          <Form.Item
            label="上下文窗口大小"
            name="context_window"
            rules={[{ required: true, message: "请输入上下文窗口大小" }]}
            extra="单位 tokens；按服务实际能力填写，连接检测不测量窗口。"
          >
            <InputNumber
              min={1024}
              max={10000000}
              precision={0}
              style={{ width: "100%" }}
            />
          </Form.Item>
          <Form.Item
            label="思考强度"
            name="thinking_effort"
            extra="默认不发送思考参数；不支持的参数会明确报错，不会自动降级。"
          >
            <Select
              options={(formSchema === "openai"
                ? [
                    "default",
                    "none",
                    "minimal",
                    "low",
                    "medium",
                    "high",
                    "xhigh",
                  ]
                : ["default", "none", "low", "medium", "high", "max"]
              ).map((value) => ({ value, label: effortNames[value] }))}
            />
          </Form.Item>
        </div>
        <div className="editor-page-actions">
          <Button href="#/models" disabled={saving}>
            取消
          </Button>
          <Button
            aria-label="保存模型配置"
            type="primary"
            htmlType="submit"
            loading={saving}
            disabled={busy && !saving}
          >
            保存模型
          </Button>
        </div>
      </Form>
    </section>
  );
}

export function ModelHistoryPage({ id }: { id: string }) {
  const [versions, setVersions] = useState<ModelVersion[]>([]);
  const [historyError, setHistoryError] = useState("");
  const [historyLoading, setHistoryLoading] = useState(true);
  useEffect(() => {
    let active = true;
    api
      .modelVersions(id)
      .then((items) => {
        if (active) setVersions(items);
      })
      .catch((e) => {
        if (active)
          setHistoryError(e instanceof Error ? e.message : "读取历史版本失败");
      })
      .finally(() => {
        if (active) setHistoryLoading(false);
      });
    return () => {
      active = false;
    };
  }, [id]);
  return (
    <>
      <Back href="#/models" label="返回模型配置" />
      <PageHeading
        title="模型历史版本"
        description={
          versions[0]?.name ?? "查看模型参数、接口和凭据状态的变更记录。"
        }
        actions={<Button href={`#/models/${id}/edit`}>编辑模型</Button>}
      />
      <section className="panel">
        {historyError && <Alert type="error" message={historyError} />}
        <Table<ModelVersion>
          loading={historyLoading}
          rowKey="revision"
          dataSource={versions}
          scroll={{ x: 700 }}
          pagination={{ pageSize: 10 }}
          columns={[
            { title: "版本", width: 65, render: (_, v) => `v${v.revision}` },
            {
              title: "名称 / ID",
              render: (_, v) => (
                <>
                  {v.name}
                  <span className="cell-caption break-all">{v.model_id}</span>
                </>
              ),
            },
            {
              title: "接口",
              render: (_, v) => (
                <>
                  {v.api_schema} · {v.provider}
                  <span className="cell-caption break-all">{v.url}</span>
                </>
              ),
            },
            {
              title: "上下文 / 思考",
              render: (_, v) => (
                <>
                  {v.context_window.toLocaleString()}
                  <span className="cell-caption">
                    {effortNames[v.thinking_effort ?? "default"]}
                  </span>
                </>
              ),
            },
            {
              title: "Key",
              render: (_, v) => (v.has_key ? "已设置" : "未设置"),
            },
            { title: "保存时间", render: (_, v) => time(v.created_at) },
          ]}
        />
      </section>
    </>
  );
}
