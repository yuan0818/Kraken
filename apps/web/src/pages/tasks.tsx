import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Checkbox,
  Form,
  Input,
  InputNumber,
  Modal,
  Radio,
  Select,
  Space,
  Switch,
  Table,
  Tabs,
  Tag,
  App as AntApp,
} from "antd";
import {
  api,
  type TaskRecord,
  type TaskDetail,
  type TaskPage,
  type TaskCase,
  type TaskConfiguration,
  type ExecutionBatch,
  type ModelRecord,
} from "@kraken/api-client";
import { navigate, usePlatform } from "../state";
import { Back, Icon, NoData, PageHeading, shortId, time } from "../ui";
import { snapshotLabel } from "../case-model";
import { TaskCasePicker } from "../task-case-picker";

const defaultSchedule = {
  enabled: false,
  frequency: "daily" as const,
  time: "02:00",
  timezone: "Asia/Taipei",
  weekdays: [0, 1, 2, 3, 4],
};
export const batchStates: Record<string, string> = {
  queued: "排队中",
  running: "执行中",
  retry_wait: "等待重试",
  completed: "已结束",
  blocked: "已阻塞",
  cancel_pending: "取消中",
  cancelled: "已取消",
  needs_recovery: "待恢复",
};
export const batchActive = (state: string) =>
  !["completed", "cancelled", "blocked"].includes(state);
export function BatchTable({
  items,
  loading = false,
  reportList,
}: {
  items: ExecutionBatch[];
  loading?: boolean;
  reportList?: {
    page: number;
    pageSize: number;
    total: number;
    onChange: (page: number) => void;
    query: string;
  };
}) {
  const reportHref = (id: string) =>
    `#/reports/${id}${reportList?.query ? `?${reportList.query}` : ""}`;
  return (
    <Table<ExecutionBatch>
      rowKey="id"
      loading={loading}
      dataSource={items}
      scroll={{ x: 850 }}
      pagination={{
        current: reportList?.page,
        total: reportList?.total,
        onChange: reportList?.onChange,
        pageSize: reportList?.pageSize ?? 10,
        hideOnSinglePage: true,
        showSizeChanger: false,
      }}
      columns={[
        {
          title: "执行批次",
          render: (_, r) => (
            <a
              className="entity-link"
              href={reportList ? reportHref(r.id) : `#/executions/${r.id}`}
            >
              <strong>
                {String(r.task_snapshot?.name ?? "历史单用例执行")}
              </strong>
              <span className="cell-caption mono">RUN-{shortId(r.id)}</span>
            </a>
          ),
        },
        {
          title: "状态",
          width: 115,
          render: (_, r) => (
            <Tag
              color={
                r.state === "blocked" || r.state === "needs_recovery"
                  ? "orange"
                  : batchActive(r.state)
                    ? "blue"
                    : "default"
              }
            >
              {batchStates[r.state] ?? r.state}
            </Tag>
          ),
        },
        {
          title: "进度",
          width: 100,
          render: (_, r) => `${r.finished} / ${r.total} 用例`,
        },
        {
          title: "触发方式",
          width: 100,
          render: (_, r) =>
            r.trigger === "scheduled"
              ? "定时"
              : r.trigger === "legacy"
                ? "历史手动"
                : "手动",
        },
        {
          title: "配置版本",
          width: 100,
          render: (_, r) => (r.task_revision ? `v${r.task_revision}` : "—"),
        },
        { title: "创建时间", width: 145, render: (_, r) => time(r.created_at) },
        {
          title: "操作",
          width: reportList ? 110 : 160,
          render: (_, r) => (
            <Space>
              {!reportList && <a href={`#/executions/${r.id}`}>查看执行</a>}
              <a href={reportHref(r.id)}>查看报告</a>
            </Space>
          ),
        },
      ]}
    />
  );
}
export function TaskCreator() {
  const { taskCase, setTaskCase, data, mutate, busy } = usePlatform();
  const [form] = Form.useForm<{ name: string; description: string }>();
  const request = useRef(crypto.randomUUID());
  const [saving, setSaving] = useState(false);
  useEffect(() => {
    if (taskCase === null) return;
    request.current = crypto.randomUUID();
    form.resetFields();
    form.setFieldsValue({
      name: taskCase
        ? `${data?.cases.find((c) => c.id === taskCase)?.name ?? "用例"}回归`
        : "",
      description: "",
    });
  }, [taskCase, form]);
  const create = async (values: { name: string; description: string }) => {
    setSaving(true);
    const task = await mutate(async () => {
      let value = await api.createTask({
        ...values,
        request_id: request.current,
      });
      if (taskCase && !value.cases.some((c) => c.id === taskCase))
        value = await api.saveTask(value.id, {
          name: value.name,
          description: value.description,
          expected_revision: value.revision,
          configuration: value.configuration,
          case_ids: [...value.cases.map((c) => c.id), taskCase],
        });
      return value;
    });
    setSaving(false);
    if (task) {
      setTaskCase(null);
      navigate(`/runs/${task.id}`);
    }
  };
  return (
    <Modal
      open={taskCase !== null}
      centered
      title="创建测试任务"
      destroyOnHidden
      onCancel={() => !saving && setTaskCase(null)}
      footer={null}
      maskClosable={!saving}
      closable={!saving}
    >
      <p className="form-intro">
        先建立任务，再添加用例、配置执行规则。创建任务不会开始设备测试。
      </p>
      <Form
        form={form}
        name="task-creator"
        layout="vertical"
        onFinish={create}
        disabled={saving}
      >
        <Form.Item
          label="任务名称"
          name="name"
          rules={[
            { required: true, whitespace: true, message: "请输入任务名称" },
          ]}
        >
          <Input maxLength={200} placeholder="例如：Android OS 每日回归" />
        </Form.Item>
        <Form.Item label="任务说明" name="description">
          <Input.TextArea
            rows={3}
            maxLength={2000}
            placeholder="描述测试范围或目标"
          />
        </Form.Item>
        {taskCase && (
          <Alert
            type="info"
            message="创建后将添加当前用例，可继续添加其他用例。"
          />
        )}
        <div className="modal-actions">
          <Button disabled={saving} onClick={() => setTaskCase(null)}>
            取消
          </Button>
          <Button
            aria-label="确认创建测试任务"
            type="primary"
            htmlType="submit"
            loading={saving}
            disabled={busy && !saving}
          >
            创建任务
          </Button>
        </div>
      </Form>
    </Modal>
  );
}
export type HistoryTask = { value: string; label: string };

export function TaskHistoryFilter({
  task,
  onTaskChange,
  label,
}: {
  task?: HistoryTask;
  onTaskChange: (task?: HistoryTask) => void;
  label: string;
}) {
  const [search, setSearch] = useState("");
  const [options, setOptions] = useState<HistoryTask[]>([]);
  const [searching, setSearching] = useState(true);
  const [searchError, setSearchError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setSearching(true);
    setSearchError("");
    const timer = setTimeout(async () => {
      try {
        const query = new URLSearchParams({
          q: search.trim(),
          page_size: "100",
        });
        const result = await api.tasks(query.toString(), controller.signal);
        if (!controller.signal.aborted) {
          setOptions(
            result.items.map(({ id, name }) => ({ value: id, label: name })),
          );
        }
      } catch (e) {
        if (!controller.signal.aborted) {
          setOptions([]);
          setSearchError(e instanceof Error ? e.message : "任务搜索失败");
        }
      } finally {
        if (!controller.signal.aborted) setSearching(false);
      }
    }, 200);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [search]);

  return (
    <div className="filter-bar">
      <Select<HistoryTask, HistoryTask>
        aria-label={label}
        allowClear
        labelInValue
        showSearch
        filterOption={false}
        placeholder="全部任务"
        style={{ width: 300 }}
        value={options.find((option) => option.value === task?.value) ?? task}
        options={searching ? [] : options}
        loading={searching}
        onSearch={setSearch}
        onChange={(value) => {
          onTaskChange(value);
          setSearch("");
        }}
        notFoundContent={
          searching ? "正在搜索任务…" : searchError || "未找到匹配任务"
        }
      />
    </div>
  );
}

function ExecutionHistory({
  task,
  onTaskChange,
}: {
  task?: HistoryTask;
  onTaskChange: (task?: HistoryTask) => void;
}) {
  const taskId = task?.value;
  const [history, setHistory] = useState<{
    taskId?: string;
    items: ExecutionBatch[];
    error?: string;
  }>();

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      try {
        const items = await api.executions(taskId, controller.signal);
        if (!controller.signal.aborted) setHistory({ taskId, items });
      } catch (e) {
        if (!controller.signal.aborted) {
          setHistory({
            taskId,
            items: [],
            error: e instanceof Error ? e.message : "读取执行记录失败",
          });
        }
      }
    };
    void load();
    const poll = setInterval(() => void load(), 5000);
    return () => {
      controller.abort();
      clearInterval(poll);
    };
  }, [taskId]);

  const current = history?.taskId === taskId ? history : undefined;
  return (
    <>
      <TaskHistoryFilter
        task={task}
        onTaskChange={onTaskChange}
        label="按任务筛选执行记录"
      />
      {current?.error && (
        <Alert
          className="connection-alert"
          type="error"
          message={current.error}
        />
      )}
      <BatchTable
        key={taskId ?? "all"}
        items={current?.items ?? []}
        loading={!current}
      />
    </>
  );
}

export function TasksPage({
  scheduleOnly = false,
}: {
  scheduleOnly?: boolean;
}) {
  const { setTaskCase, busy } = usePlatform();
  const [q, setQ] = useState("");
  const [page, setPage] = useState(1);
  const [tab, setTab] = useState("tasks");
  const [result, setResult] = useState<TaskPage>();
  const [historyTask, setHistoryTask] = useState<HistoryTask>();
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    let alive = true;
    const controller = new AbortController();
    const load = async () => {
      try {
        const tasks = await api.tasks(
          new URLSearchParams({
            q,
            page: String(page),
            page_size: "20",
            scheduled: String(scheduleOnly),
          }).toString(),
          controller.signal,
        );
        if (alive) {
          setResult(tasks);
          setError("");
        }
      } catch (e) {
        if (alive) setError(e instanceof Error ? e.message : "读取失败");
      } finally {
        if (alive) setLoading(false);
      }
    };
    const debounce = setTimeout(() => void load(), 200);
    const poll = setInterval(() => void load(), 5000);
    return () => {
      alive = false;
      controller.abort();
      clearTimeout(debounce);
      clearInterval(poll);
    };
  }, [page, q, scheduleOnly]);
  return (
    <>
      <PageHeading
        eyebrow="TEST TASKS"
        title={scheduleOnly ? "定时计划" : "测试任务"}
        description="任务保存用例集合与执行规则，每次手动或定时触发都会形成独立执行批次。"
        actions={
          <Button
            type="primary"
            icon={<Icon name="plus" />}
            disabled={busy}
            onClick={() => setTaskCase("")}
          >
            新建测试任务
          </Button>
        }
      />
      {error && (
        <Alert className="connection-alert" type="error" message={error} />
      )}
      <section className="panel list-panel">
        <Tabs
          className="task-tabs"
          activeKey={tab}
          onChange={setTab}
          items={[
            { key: "tasks", label: "任务配置" },
            { key: "history", label: "执行记录" },
          ]}
        />
        {tab === "tasks" ? (
          <>
            <div className="filter-bar">
              <Input
                className="search-input"
                aria-label="搜索测试任务"
                prefix={<Icon name="search" size={16} />}
                placeholder="搜索任务名称或说明"
                value={q}
                allowClear
                onChange={(e) => {
                  setQ(e.target.value);
                  setPage(1);
                }}
              />
              <span className="muted">共 {result?.total ?? 0} 个任务</span>
            </div>
            <Table<TaskRecord>
              className="platform-table"
              rowKey="id"
              loading={loading}
              dataSource={result?.items ?? []}
              scroll={{ x: 1000 }}
              pagination={{
                current: page,
                pageSize: 20,
                total: result?.total ?? 0,
                onChange: setPage,
                showSizeChanger: false,
                hideOnSinglePage: true,
              }}
              locale={{
                emptyText: <NoData text="创建第一个任务，然后添加测试用例" />,
              }}
              columns={[
                {
                  title: "任务名称",
                  width: 240,
                  render: (_, t) => (
                    <a className="entity-link" href={`#/runs/${t.id}`}>
                      <strong>{t.name}</strong>
                      <span className="cell-caption">
                        {t.description || "暂无说明"}
                      </span>
                    </a>
                  ),
                },
                { title: "用例数", width: 80, dataIndex: "case_count" },
                {
                  title: "执行方式",
                  width: 155,
                  render: (_, t) => (
                    <>
                      {t.configuration.schedule?.enabled ? (
                        <Tag color="blue">
                          {t.configuration.schedule.frequency === "weekly"
                            ? "每周"
                            : "每天"}{" "}
                          {t.configuration.schedule.time}
                        </Tag>
                      ) : (
                        <Tag>手动执行</Tag>
                      )}
                      <span className="cell-caption">
                        {t.configuration.execution_order === "serial"
                          ? "顺序执行"
                          : `最多 ${t.configuration.max_parallel} 个并发`}
                      </span>
                    </>
                  ),
                },
                {
                  title: "刷机 / 重试",
                  width: 140,
                  render: (_, t) => (
                    <>
                      {t.configuration.require_flash ? "需要刷机" : "无需刷机"}
                      <span className="cell-caption">
                        {t.configuration.max_retries
                          ? `最多重试 ${t.configuration.max_retries} 次`
                          : "不自动重试"}
                      </span>
                    </>
                  ),
                },
                {
                  title: "下次触发",
                  width: 160,
                  render: (_, t) =>
                    t.next_trigger_at ? (
                      <>
                        <span>
                          {new Date(t.next_trigger_at).toLocaleString("zh-CN", {
                            timeZone:
                              t.configuration.schedule?.timezone ??
                              "Asia/Taipei",
                            month: "2-digit",
                            day: "2-digit",
                            hour: "2-digit",
                            minute: "2-digit",
                            hour12: false,
                          })}
                        </span>
                        <span className="cell-caption">
                          {t.configuration.schedule?.timezone}
                        </span>
                      </>
                    ) : (
                      "—"
                    ),
                },
                {
                  title: "操作",
                  width: 110,
                  render: (_, t) => <a href={`#/runs/${t.id}`}>管理与执行</a>,
                },
              ]}
            />
          </>
        ) : (
          <ExecutionHistory task={historyTask} onTaskChange={setHistoryTask} />
        )}
      </section>
    </>
  );
}
export function TaskDetailPage({ id }: { id: string }) {
  const { busy, refresh, data } = usePlatform();
  const { message } = AntApp.useApp();
  const [task, setTask] = useState<TaskDetail>();
  const [draft, setDraft] = useState<TaskDetail>();
  const [dirty, setDirty] = useState(false);
  const [picker, setPicker] = useState(false);
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const [runs, setRuns] = useState<ExecutionBatch[]>([]);
  const [models, setModels] = useState<ModelRecord[]>([]);
  const [modelError, setModelError] = useState("");
  useEffect(() => {
    let active = true;
    api
      .models()
      .then((v) => {
        if (active) setModels(v);
      })
      .catch((e) => {
        if (active) setModelError(e.message);
      });
    return () => {
      active = false;
    };
  }, []);
  const triggerRequest = useRef<{ revision: number; id: string } | undefined>(
    undefined,
  );
  const load = async () => {
    try {
      const value = await api.task(id);
      setTask(value);
      setDraft(value);
      setDirty(false);
      setError("");
    } catch (e) {
      setError(e instanceof Error ? e.message : "读取失败");
    }
  };
  useEffect(() => {
    void load();
  }, [id]);
  useEffect(() => {
    let active = true;
    const fetch = () =>
      api
        .executions(id)
        .then((v) => {
          if (active) setRuns(v);
        })
        .catch((e) => {
          if (active) setError(e.message);
        });
    void fetch();
    const timer = setInterval(() => void fetch(), 3000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [id]);
  useEffect(() => {
    const guard = (e: BeforeUnloadEvent) => {
      if (dirty) {
        e.preventDefault();
      }
    };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);
  if (!task || !draft)
    return (
      <>
        <Back href="#/runs" label="返回测试任务" />
        {error ? (
          <Alert
            type="error"
            message={error}
            action={<Button onClick={() => void load()}>重试</Button>}
          />
        ) : (
          <NoData text="正在读取任务…" />
        )}
      </>
    );
  const update = (patch: Partial<TaskDetail>) => {
    setDraft({ ...draft, ...patch });
    setDirty(true);
  };
  const config = (patch: Partial<TaskConfiguration>) =>
    update({ configuration: { ...draft.configuration, ...patch } });
  const schedule = (
    patch: Partial<NonNullable<TaskConfiguration["schedule"]>>,
  ) =>
    config({
      schedule: {
        ...defaultSchedule,
        ...draft.configuration.schedule,
        ...patch,
      },
    });
  const disabled = busy || working;
  const c = draft.configuration;
  const s = c.schedule ?? defaultSchedule;
  const save = async () => {
    setWorking(true);
    setError("");
    try {
      const value = await api.saveTask(id, {
        expected_revision: task.revision,
        name: draft.name,
        description: draft.description,
        case_ids: draft.cases.map((v) => v.id),
        configuration: c,
      });
      setTask(value);
      setDraft(value);
      setDirty(false);
      void message.success("任务配置已保存，已有执行批次保持不变");
    } catch (e) {
      setError(e instanceof Error ? e.message : "保存失败");
    } finally {
      setWorking(false);
    }
  };
  const execute = async () => {
    setWorking(true);
    setError("");
    try {
      if (triggerRequest.current?.revision !== task.revision)
        triggerRequest.current = {
          revision: task.revision,
          id: crypto.randomUUID(),
        };
      const value = await api.executeTask(
        id,
        triggerRequest.current.id,
        task.revision,
      );
      triggerRequest.current = undefined;
      await refresh();
      navigate(`/executions/${value.id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "执行失败");
    } finally {
      setWorking(false);
    }
  };
  const move = (index: number, offset: number) => {
    const values = [...draft.cases];
    [values[index], values[index + offset]] = [
      values[index + offset],
      values[index],
    ];
    update({ cases: values });
  };
  return (
    <>
      <Back href="#/runs" label="返回测试任务" />
      <PageHeading
        title={task.name}
        description={`配置 v${task.revision} · ${draft.cases.length} 个用例 · ${dirty ? "有未保存的修改" : "已保存"}`}
        actions={
          <>
            <Button
              disabled={disabled || !dirty}
              loading={working}
              onClick={() => void save()}
            >
              保存任务
            </Button>
            <Button
              type="primary"
              icon={<Icon name="play" />}
              disabled={
                disabled ||
                dirty ||
                !task.cases.length ||
                !!task.configuration.require_flash
              }
              onClick={() => void execute()}
            >
              立即执行
            </Button>
          </>
        }
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
      {dirty && (
        <Alert
          className="connection-alert"
          type="info"
          showIcon
          message="保存任务后，新增用例和执行规则才会生效。未保存时不能立即执行。"
        />
      )}
      <section className="panel task-basics">
        <div className="panel-heading">
          <h2>任务信息</h2>
          <Tag>用例集合</Tag>
        </div>
        <Form layout="vertical" className="task-basics-form">
          <Form.Item label="任务名称" required>
            <Input
              aria-label="编辑任务名称"
              maxLength={200}
              disabled={disabled}
              value={draft.name}
              onChange={(e) => update({ name: e.target.value })}
            />
          </Form.Item>
          <Form.Item label="任务说明">
            <Input
              aria-label="编辑任务说明"
              maxLength={2000}
              disabled={disabled}
              value={draft.description}
              onChange={(e) => update({ description: e.target.value })}
            />
          </Form.Item>
        </Form>
      </section>
      <section className="panel task-members">
        <div className="panel-heading">
          <div>
            <h2>
              测试用例 <span className="count-badge">{draft.cases.length}</span>
            </h2>
            <p>
              可调整顺序或移除。顺序执行时按列表顺序运行，每次尝试开始时读取当前
              Stable。
            </p>
          </div>
          <Button
            icon={<Icon name="plus" />}
            disabled={disabled}
            onClick={() => setPicker(true)}
          >
            添加用例
          </Button>
        </div>
        <Table<TaskCase>
          className="platform-table"
          rowKey="id"
          dataSource={draft.cases}
          scroll={{ x: 900 }}
          pagination={{
            pageSize: 10,
            hideOnSinglePage: true,
            showSizeChanger: false,
          }}
          locale={{
            emptyText: <NoData text="任务中还没有用例，点击添加用例开始配置" />,
          }}
          columns={[
            {
              title: "编号 / 用例",
              width: 240,
              render: (_, r) => (
                <a href={`#/cases/${r.id}`}>
                  <strong>{r.code}</strong>
                  <span className="cell-caption">{r.name}</span>
                </a>
              ),
            },
            {
              title: "模块",
              width: 150,
              render: (_, r) =>
                r.latest_content
                  ? snapshotLabel(r.latest_content, r.latest_content.module_id)
                  : "—",
            },
            {
              title: "测试目的",
              render: (_, r) => (
                <span className="case-purpose-cell">
                  {r.latest_content?.purpose || r.latest_content?.title}
                </span>
              ),
            },
            {
              title: "Stable",
              width: 130,
              render: (_, r) =>
                r.stable_ready ? (
                  <Tag color="green">可执行</Tag>
                ) : (
                  <a href={`#/cases/${r.id}`}>待设置 Stable</a>
                ),
            },
            {
              title: "操作",
              width: 160,
              render: (_, r) => {
                const index = draft.cases.findIndex((c) => c.id === r.id);
                return (
                  <Space size={0}>
                    <Button
                      type="text"
                      size="small"
                      aria-label={`上移 ${r.code}`}
                      disabled={disabled || index === 0}
                      onClick={() => move(index, -1)}
                    >
                      上移
                    </Button>
                    <Button
                      type="text"
                      size="small"
                      aria-label={`下移 ${r.code}`}
                      disabled={disabled || index === draft.cases.length - 1}
                      onClick={() => move(index, 1)}
                    >
                      下移
                    </Button>
                    <Button
                      type="link"
                      danger
                      size="small"
                      disabled={disabled}
                      onClick={() =>
                        update({
                          cases: draft.cases.filter((c) => c.id !== r.id),
                        })
                      }
                    >
                      移除
                    </Button>
                  </Space>
                );
              },
            },
          ]}
        />
      </section>
      <section className="panel task-policy">
        <div className="panel-heading">
          <div>
            <h2>执行配置</h2>
            <p>
              同一任务的批次依次执行；每轮使用触发时保存的配置，后续修改仅影响新批次。
            </p>
          </div>
        </div>
        <div className="task-policy-grid">
          <div className="task-policy-card">
            <h3>执行目标</h3>
            <Select
              aria-label="执行设备"
              style={{ width: "100%" }}
              value={c.target_device_id ?? "simulation"}
              disabled={disabled}
              onChange={(v) =>
                config({
                  target_device_id: v === "simulation" ? null : v,
                })
              }
              options={[
                { value: "simulation", label: "模拟执行" },
                ...(data?.devices
                  .filter((d) => d.artemis_state)
                  .map((d) => ({
                    value: d.id,
                    label: d.serial + " · " + (d.adb_model ?? d.node_id),
                  })) ?? []),
              ]}
            />
            <p>
              {c.target_device_id ? "设备忙时排队。" : "验证平台任务流程。"}
            </p>
            <h3>执行模型</h3>
            <Form layout="vertical">
              <Form.Item label="模型配置（可选）">
                <Select
                  aria-label="任务执行模型"
                  allowClear
                  showSearch
                  optionFilterProp="label"
                  placeholder={
                    c.target_device_id
                      ? "未选择（使用节点默认模型）"
                      : "选择要记录到批次的模型配置"
                  }
                  value={c.model_config_id ?? undefined}
                  disabled={disabled || !!modelError}
                  onChange={(v) => config({ model_config_id: v ?? null })}
                  options={models.map((m) => ({
                    value: m.id,
                    label: `${m.name} · ${m.model_id} · v${m.revision}${m.enabled ? "" : "（已停用）"}`,
                    disabled: !m.enabled,
                  }))}
                />
              </Form.Item>
              {modelError && <Alert type="error" message={modelError} />}
              <p>
                {c.target_device_id
                  ? "真机批次使用触发时选定的模型与凭据版本。后续配置修改仅影响新批次。"
                  : "触发时记录所选模型版本和凭据版本。模拟批次不会调用模型。"}
              </p>
              <a href="#/models">管理模型配置</a>
            </Form>
          </div>
          <div className="task-policy-card">
            <h3>调度方式</h3>
            <Form layout="vertical">
              <Form.Item label="用例执行顺序">
                <Radio.Group
                  aria-label="用例执行顺序"
                  disabled={disabled}
                  value={c.execution_order}
                  onChange={(e) => config({ execution_order: e.target.value })}
                  options={[
                    { label: "并发执行", value: "parallel" },
                    { label: "顺序执行", value: "serial" },
                  ]}
                />
              </Form.Item>
              {c.execution_order === "parallel" && (
                <Form.Item label="最大并发用例数">
                  <InputNumber
                    aria-label="最大并发用例数"
                    min={1}
                    max={60}
                    value={c.max_parallel}
                    disabled={disabled}
                    onChange={(v) => config({ max_parallel: v ?? 1 })}
                  />
                </Form.Item>
              )}
              <p className="muted">
                每部设备独占。可用设备不足时排队，不超出此并发上限。
              </p>
            </Form>
          </div>
          <div className="task-policy-card">
            <div className="task-policy-switch">
              <h3>定时执行</h3>
              <Switch
                aria-label="启用定时执行"
                checked={s.enabled}
                disabled={disabled}
                onChange={(v) => schedule({ enabled: v })}
              />
            </div>
            {s.enabled ? (
              <Form layout="vertical">
                <Form.Item label="频率">
                  <Radio.Group
                    aria-label="定时频率"
                    value={s.frequency}
                    disabled={disabled}
                    onChange={(e) => schedule({ frequency: e.target.value })}
                    options={[
                      { label: "每天", value: "daily" },
                      { label: "每周", value: "weekly" },
                    ]}
                  />
                </Form.Item>
                {s.frequency === "weekly" && (
                  <Form.Item label="执行日">
                    <Checkbox.Group
                      disabled={disabled}
                      value={s.weekdays}
                      onChange={(v) => schedule({ weekdays: v as number[] })}
                      options={[
                        "周一",
                        "周二",
                        "周三",
                        "周四",
                        "周五",
                        "周六",
                        "周日",
                      ].map((label, value) => ({ value, label }))}
                    />
                  </Form.Item>
                )}
                <div className="task-inline-fields">
                  <Form.Item label="执行时间">
                    <Input
                      aria-label="定时执行时间"
                      type="time"
                      disabled={disabled}
                      value={s.time}
                      onChange={(e) => schedule({ time: e.target.value })}
                    />
                  </Form.Item>
                  <Form.Item label="时区">
                    <Select
                      aria-label="定时时区"
                      disabled={disabled}
                      value={s.timezone}
                      onChange={(v) => schedule({ timezone: v })}
                      options={["Asia/Taipei", "Asia/Shanghai", "UTC"].map(
                        (value) => ({ value, label: value }),
                      )}
                    />
                  </Form.Item>
                </div>
                <p className="muted">
                  上一轮未结束时排队；服务恢复只补最近一次错过的触发。
                </p>
              </Form>
            ) : (
              <p className="muted">
                当前仅手动执行。启用后仍可随时手动创建执行批次。
              </p>
            )}
          </div>
          <div className="task-policy-card">
            <div className="task-policy-switch">
              <h3>执行前刷机</h3>
              <Switch
                aria-label="执行前需要刷机"
                checked={c.require_flash}
                disabled={disabled}
                onChange={(v) => config({ require_flash: v })}
              />
            </div>
            {c.require_flash ? (
              <>
                <Form layout="vertical">
                  <Form.Item label="Fastboot 全量刷机包来源" required>
                    <Input
                      aria-label="刷机包来源"
                      disabled={disabled}
                      value={c.flash_source}
                      onChange={(e) => config({ flash_source: e.target.value })}
                      placeholder="内网 SMB 或 FTP 地址，不含账号密码"
                    />
                  </Form.Item>
                </Form>
                <Alert
                  showIcon
                  type="warning"
                  message="刷机能力待接入"
                  description="可保存刷机要求；当前执行会被阻止，定时触发会记录阻塞原因，不会跳过刷机。"
                />
              </>
            ) : (
              <p className="muted">沿用设备现有系统，不进行刷机操作。</p>
            )}
          </div>
          <div className="task-policy-card">
            <div className="task-policy-switch">
              <h3>失败重试</h3>
              <Switch
                aria-label="启用失败重试"
                checked={!!c.max_retries}
                disabled={disabled}
                onChange={(v) => config({ max_retries: v ? 1 : 0 })}
              />
            </div>
            {!!c.max_retries ? (
              <Form layout="vertical">
                <div className="task-inline-fields">
                  <Form.Item label="最大重试次数（不含首跑）">
                    <InputNumber
                      aria-label="最大重试次数"
                      min={1}
                      max={10}
                      disabled={disabled}
                      value={c.max_retries}
                      onChange={(v) => config({ max_retries: v ?? 1 })}
                    />
                  </Form.Item>
                  <Form.Item label="重试间隔（秒）">
                    <InputNumber
                      aria-label="重试间隔秒数"
                      min={0}
                      max={86400}
                      disabled={disabled}
                      value={c.retry_interval_seconds}
                      onChange={(v) =>
                        config({ retry_interval_seconds: v ?? 0 })
                      }
                    />
                  </Form.Item>
                </div>
                <p className="muted">
                  断言失败、阻塞或证据不足可重试。仅在已结束、清理确认且释放设备后开始；取消和状态未知不自动重试。每次读取当前
                  Stable，保留全部尝试。
                </p>
              </Form>
            ) : (
              <p className="muted">每个用例执行一次，不自动重试。</p>
            )}
          </div>
        </div>
        <div className="task-save-bar">
          <span>{dirty ? "配置尚未保存" : "配置已保存"}</span>
          <Button
            type="primary"
            aria-label="保存任务配置"
            loading={working}
            disabled={disabled || !dirty}
            onClick={() => void save()}
          >
            保存任务配置
          </Button>
        </div>
      </section>
      <section className="panel">
        <div className="panel-heading">
          <div>
            <h2>执行记录</h2>
            <p>
              本任务最近 100
              个批次。历史批次中的用例集合和规则不随任务编辑而改变。
            </p>
          </div>
        </div>
        <BatchTable items={runs} />
      </section>
      {picker && (
        <TaskCasePicker
          existing={draft.cases}
          onClose={() => setPicker(false)}
          onAdd={(cases) => {
            update({ cases: [...draft.cases, ...cases] });
            setPicker(false);
          }}
        />
      )}
    </>
  );
}
