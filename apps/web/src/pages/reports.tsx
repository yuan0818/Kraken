import { useEffect, useState } from "react";
import { Alert, App, Button, Dropdown, Table, Tag } from "antd";
import { api, type BatchReport, type ReportPage } from "@kraken/api-client";
import { navigate, usePlatform } from "../state";
import {
  Back,
  NoData,
  PageHeading,
  Panel,
  Status,
  shortId,
  states,
  time,
  verdicts,
} from "../ui";
import {
  BatchTable,
  TaskHistoryFilter,
  batchActive,
  batchStates,
  type HistoryTask,
} from "./tasks";

const pageSize = 20;
const reportStates: Record<string, string> = {
  ...states,
  not_started: "未执行",
  retry_wait: "等待重试",
};
const verdictColors: Record<string, string> = {
  pass: "green",
  fail: "red",
  blocked: "orange",
  inconclusive: "gold",
};

export function ReportsPage() {
  const { route } = usePlatform();
  const taskId = route.query.get("task");
  const task = taskId
    ? { value: taskId, label: route.query.get("task_name") || taskId }
    : undefined;
  const rawPage = Number(route.query.get("page") ?? 1);
  const page = Number.isSafeInteger(rawPage) && rawPage > 0 ? rawPage : 1;
  const query = new URLSearchParams({
    page: String(page),
    page_size: String(pageSize),
  });
  if (taskId) query.set("task_id", taskId);
  const request = query.toString();
  const [result, setResult] = useState<{
    request: string;
    data?: ReportPage;
    error?: string;
  }>();

  useEffect(() => {
    const controller = new AbortController();
    void api.reports(request, controller.signal).then(
      (data) => {
        if (!controller.signal.aborted) setResult({ request, data });
      },
      (error: Error) => {
        if (!controller.signal.aborted)
          setResult({ request, error: error.message });
      },
    );
    return () => controller.abort();
  }, [request]);

  const change = (nextTask?: HistoryTask, nextPage = 1) => {
    const params = new URLSearchParams();
    if (nextTask) {
      params.set("task", nextTask.value);
      params.set("task_name", nextTask.label);
    }
    if (nextPage > 1) params.set("page", String(nextPage));
    navigate(params.size ? `/reports?${params}` : "/reports");
  };
  const current = result?.request === request ? result : undefined;

  return (
    <>
      <PageHeading
        eyebrow="TEST REPORTS"
        title="测试报告"
        description="按执行批次查看结果汇总、逐项用例结论和尝试证据。"
      />
      <Panel title="批次报告">
        <TaskHistoryFilter
          task={task}
          onTaskChange={change}
          label="按任务筛选测试报告"
        />
        {current?.error && (
          <Alert
            className="connection-alert"
            type="error"
            message={current.error}
          />
        )}
        <BatchTable
          items={current?.data?.items ?? []}
          loading={!current}
          reportList={{
            page,
            pageSize,
            total: current?.data?.total ?? 0,
            query: route.query.toString(),
            onChange: (nextPage) => change(task, nextPage),
          }}
        />
      </Panel>
    </>
  );
}

export function ReportDetail({ id }: { id: string }) {
  const { route } = usePlatform();
  const { message } = App.useApp();
  const [exporting, setExporting] = useState(false);
  const query = route.query.toString();
  const [report, setReport] = useState<BatchReport>();
  const [error, setError] = useState("");

  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      try {
        const data = await api.report(id, controller.signal);
        if (controller.signal.aborted) return;
        setReport(data);
        setError("");
        if (batchActive(data.state))
          timer = setTimeout(() => void load(), 3000);
      } catch (e) {
        if (controller.signal.aborted) return;
        setError(e instanceof Error ? e.message : "读取报告失败");
        timer = setTimeout(() => void load(), 5000);
      }
    };
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [id]);

  const download = async (format: "html" | "json") => {
    setExporting(true);
    try {
      const response = await api.exportReport(id, format);
      const filename = response.headers
        .get("Content-Disposition")
        ?.match(/filename="([^"]+)"/)?.[1];
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = filename ?? `RUN-${shortId(id)}.${format}`;
      link.click();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (e) {
      void message.error(e instanceof Error ? e.message : "导出失败");
    } finally {
      setExporting(false);
    }
  };

  const heading = report
    ? String(report.task_snapshot?.name ?? "历史单用例执行")
    : "测试报告";
  const active = report && batchActive(report.state);
  const verdictLabel = active ? "最新结论" : "最终结论";
  const attemptHref = (caseId: string, attemptId: string) => {
    const params = new URLSearchParams({
      case: caseId,
      attempt: attemptId,
      from: "report",
    });
    if (query) params.set("report_query", query);
    return `#/executions/${id}?${params}`;
  };

  return (
    <>
      <Back
        href={query ? `#/reports?${query}` : "#/reports"}
        label="返回测试报告列表"
      />
      <PageHeading
        eyebrow="BATCH REPORT"
        title={heading}
        description={
          report
            ? `RUN-${shortId(id)} · ${time(report.created_at)}`
            : `RUN-${shortId(id)}`
        }
        actions={
          report && (
            <>
              <Button href={`#/executions/${id}`}>查看执行记录</Button>
              <Dropdown
                trigger={["click"]}
                disabled={exporting}
                menu={{
                  items: [
                    { key: "html", label: "HTML 报告" },
                    { key: "json", label: "JSON 数据" },
                  ],
                  onClick: ({ key }) =>
                    void download(key === "json" ? "json" : "html"),
                }}
              >
                <Button type="primary" loading={exporting}>
                  导出批次
                </Button>
              </Dropdown>
            </>
          )
        }
      />
      {error && (
        <Alert className="connection-alert" type="error" message={error} />
      )}
      {!report && !error && <NoData text="正在读取批次报告…" />}
      {report && (
        <>
          {report.reason && (
            <Alert
              className="connection-alert"
              type="warning"
              showIcon
              message={report.reason}
            />
          )}
          <Panel
            title="结果汇总"
            description={`每项用例按${verdictLabel}计入一次。`}
          >
            <div className="report-summary">
              <div className="report-progress">
                <Status
                  state={report.state}
                  label={batchStates[report.state]}
                />
                <span>
                  已结束{" "}
                  <strong>
                    {report.finished} / {report.total}
                  </strong>{" "}
                  项
                </span>
              </div>
              <div className="report-outcomes">
                {[
                  "pass",
                  "fail",
                  "blocked",
                  "inconclusive",
                  "not_evaluated",
                ].map((verdict) => (
                  <div key={verdict}>
                    <Tag color={verdictColors[verdict]}>
                      {verdicts[verdict]}
                    </Tag>
                    <strong>{report.summary.verdicts[verdict] ?? 0}</strong>
                  </div>
                ))}
              </div>
              <div className="report-facts">
                <span>
                  首跑通过 <strong>{report.summary.first_pass}</strong> 项
                </span>
                <span>
                  已重试 <strong>{report.summary.retried}</strong> 项
                </span>
                <span>
                  重试后通过{" "}
                  <strong>{report.summary.passed_after_retry}</strong> 项
                </span>
                <span>
                  尝试记录 <strong>{report.summary.attempts}</strong> 次
                </span>
              </div>
              <div className="report-facts">
                <strong>用例执行状态</strong>
                {Object.entries(report.summary.states).map(([state, count]) => (
                  <span key={state}>
                    {reportStates[state] ?? state} <strong>{count}</strong> 项
                  </span>
                ))}
              </div>
            </div>
          </Panel>
          <Panel
            title={`用例结果（${report.cases.length}）`}
            className="execution-cases"
          >
            <Table<BatchReport["cases"][number]>
              rowKey="case_id"
              dataSource={report.cases}
              scroll={{ x: 960 }}
              locale={{ emptyText: "本批次没有用例记录" }}
              pagination={{
                pageSize: 10,
                hideOnSinglePage: true,
                showSizeChanger: false,
              }}
              columns={[
                {
                  title: "用例",
                  render: (_, c) => {
                    const name = (
                      <>
                        <strong>{c.case_name}</strong>
                        <span className="cell-caption mono">{c.code}</span>
                      </>
                    );
                    return c.attempt_id ? (
                      <a
                        className="entity-link"
                        href={attemptHref(c.case_id, c.attempt_id)}
                      >
                        {name}
                      </a>
                    ) : (
                      <span className="entity-link">{name}</span>
                    );
                  },
                },
                {
                  title: "执行状态",
                  width: 110,
                  render: (_, c) => (
                    <Status state={c.state} label={reportStates[c.state]} />
                  ),
                },
                {
                  title: "首跑结论",
                  width: 115,
                  render: (_, c) => verdicts[c.first_verdict],
                },
                {
                  title: verdictLabel,
                  width: 115,
                  render: (_, c) => (
                    <Tag color={verdictColors[c.verdict]}>
                      {verdicts[c.verdict]}
                    </Tag>
                  ),
                },
                { title: "尝试次数", width: 95, dataIndex: "attempts" },
                {
                  title: "结果说明",
                  width: 245,
                  render: (_, c) => (
                    <span className="report-reason">{c.reason || "—"}</span>
                  ),
                },
                {
                  title: "执行证据",
                  width: 100,
                  render: (_, c) =>
                    c.attempt_id ? (
                      <a href={attemptHref(c.case_id, c.attempt_id)}>
                        查看尝试
                      </a>
                    ) : (
                      "—"
                    ),
                },
              ]}
            />
          </Panel>
        </>
      )}
    </>
  );
}
