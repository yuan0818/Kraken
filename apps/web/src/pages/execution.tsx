import { useEffect, useRef, useState } from "react";
import {
  Alert,
  Button,
  Modal,
  Pagination,
  Select,
  Space,
  Table,
  Tag,
} from "antd";
import {
  api,
  type BatchDetail,
  type ExecutionReport,
  type TaskAttempt,
} from "@kraken/api-client";
import {
  Back,
  PageHeading,
  NoData,
  Panel,
  Status,
  time,
  shortId,
  verdicts,
  cleanupLabels,
  evidenceLabels,
} from "../ui";
import { batchActive, batchStates } from "./tasks";
import { usePlatform } from "../state";

export function ExecutionPage({ id }: { id: string }) {
  const { busy, refresh, route } = usePlatform();
  const linkedCase = route.query.get("case") ?? undefined;
  const linkedAttempt = route.query.get("attempt") ?? undefined;
  const fromReport = route.query.get("from") === "report";
  const reportQuery = route.query.get("report_query");
  const reportHref = `#/reports/${id}${reportQuery ? `?${reportQuery}` : ""}`;
  const [run, setRun] = useState<BatchDetail>();
  const [error, setError] = useState("");
  const [working, setWorking] = useState(false);
  const [caseId, setCaseId] = useState(linkedCase);
  const [attemptId, setAttemptId] = useState(linkedAttempt);
  const reportRef = useRef<HTMLElement>(null);
  useEffect(() => {
    let active = true;
    const load = async () => {
      try {
        const value = await api.execution(id);
        if (active) {
          setRun(value);
          setError("");
        }
      } catch (e) {
        if (active) setError(e instanceof Error ? e.message : "读取失败");
      }
    };
    void load();
    const timer = setInterval(() => void load(), 2000);
    return () => {
      active = false;
      clearInterval(timer);
    };
  }, [id]);
  useEffect(() => {
    if (run?.id && linkedCase) {
      reportRef.current?.scrollIntoView({ block: "start" });
    }
  }, [run?.id, linkedCase]);
  if (!run)
    return (
      <>
        <Back
          href={fromReport ? reportHref : "#/runs"}
          label={fromReport ? "返回测试报告" : "返回测试任务"}
        />
        {error ? (
          <Alert type="error" message={error} />
        ) : (
          <NoData text="正在读取执行批次…" />
        )}
      </>
    );
  const current = run.cases.find((c) => c.case_id === caseId) ?? run.cases[0];
  const attempt =
    current?.attempts.find((a) => a.id === attemptId) ?? current?.attempts[0];
  const showReport = (selectedCaseId: string) => {
    setCaseId(selectedCaseId);
    setAttemptId(undefined);
    reportRef.current?.scrollIntoView({ block: "start" });
  };
  const allIncidents = run.cases.flatMap((c) =>
    c.attempts.flatMap((a) => a.diagnostics?.incidents ?? []),
  );
  const anrCount = allIncidents.filter(
    (incident) => incident.kind === "anr",
  ).length;
  const crashCount = allIncidents.length - anrCount;
  const cancel = async () => {
    setWorking(true);
    try {
      setRun(await api.cancelExecution(id));
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "取消失败");
    } finally {
      setWorking(false);
    }
  };
  const retry = async () => {
    if (!attempt) return;
    setWorking(true);
    try {
      await api.retry(attempt.id);
      setAttemptId(undefined);
      setRun(await api.execution(id));
      await refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "重跑失败");
    } finally {
      setWorking(false);
    }
  };
  const config = run.task_snapshot?.configuration as
    | {
        require_flash?: boolean;
        max_retries?: number;
        execution_order?: string;
        max_parallel?: number;
        target_device_id?: string;
      }
    | undefined;
  return (
    <>
      <Back
        href={
          fromReport
            ? reportHref
            : run.task_id
              ? `#/runs/${run.task_id}`
              : "#/runs"
        }
        label={
          fromReport
            ? "返回测试报告"
            : run.task_id
              ? "返回任务配置"
              : "返回测试任务"
        }
      />
      <PageHeading
        title={String(run.task_snapshot?.name ?? "历史单用例执行")}
        description={`RUN-${shortId(id)} · ${run.trigger === "scheduled" ? "定时触发" : "手动执行"} · ${time(run.created_at)}${run.task_revision ? ` · 配置 v${run.task_revision}` : ""}`}
        actions={
          <>
            {!fromReport && <Button href={reportHref}>查看测试报告</Button>}
            {batchActive(run.state) && !run.cancel_requested && (
              <Button disabled={busy || working} onClick={() => void cancel()}>
                取消本批次
              </Button>
            )}
            <Tag color={batchActive(run.state) ? "blue" : "default"}>
              {batchStates[run.state]}
            </Tag>
          </>
        }
      />
      {error && (
        <Alert className="connection-alert" type="error" message={error} />
      )}
      {run.reason && (
        <Alert
          className="connection-alert"
          type="warning"
          showIcon
          message="本批次已阻塞"
          description={run.reason}
        />
      )}
      <div className="summary-strip">
        <span>
          模型：
          {run.task_snapshot?.model
            ? `${(run.task_snapshot.model as { name: string }).name} · v${(run.task_snapshot.model as { revision: number }).revision}`
            : config?.target_device_id
              ? "节点 Artemis 模型"
              : "未配置（模拟执行）"}
        </span>
        <span>
          完成{" "}
          <strong>
            {run.finished} / {run.total}
          </strong>{" "}
          用例
        </span>
        <span>
          {config?.execution_order === "serial"
            ? "顺序执行"
            : `并发上限 ${config?.max_parallel ?? 1}`}
        </span>
        <span>{config?.require_flash ? "要求刷机" : "不刷机"}</span>
        {allIncidents.length > 0 && (
          <span>
            稳定性事件{" "}
            <strong>
              {anrCount} ANR · {crashCount} Crash
            </strong>
          </span>
        )}
        <span>
          最多重试 <strong>{config?.max_retries ?? 0}</strong> 次
        </span>
      </div>
      <section className="panel execution-cases">
        <div className="panel-heading">
          <div>
            <h2>用例执行进度</h2>
            <p>每个用例独立记录尝试。批次结束与断言通过分别呈现。</p>
          </div>
        </div>
        <Table<BatchDetail["cases"][number]>
          rowKey="case_id"
          dataSource={run.cases}
          scroll={{ x: 900 }}
          pagination={{
            pageSize: 10,
            hideOnSinglePage: true,
            showSizeChanger: false,
          }}
          columns={[
            {
              title: "用例",
              render: (_, c) => (
                <Button
                  type="link"
                  aria-controls="execution-report"
                  onClick={() => showReport(c.case_id)}
                >
                  {c.code} · {c.case_name}
                </Button>
              ),
            },
            {
              title: "状态",
              width: 120,
              render: (_, c) => (
                <Status state={c.attempts[0]?.state ?? "queued"} />
              ),
            },
            {
              title: "断言结论",
              width: 120,
              render: (_, c) =>
                verdicts[c.attempts[0]?.verdict ?? "not_evaluated"],
            },
            {
              title: "稳定性事件",
              width: 120,
              render: (_, c) => {
                const count = c.attempts.reduce(
                  (total, a) => total + (a.diagnostics?.incidents?.length ?? 0),
                  0,
                );
                return count > 0 ? <Tag color="volcano">{count} 条</Tag> : "—";
              },
            },
            {
              title: "尝试次数",
              width: 100,
              render: (_, c) => c.attempts.length,
            },
            {
              title: "操作",
              width: 200,
              render: (_, c) => (
                <Space size={0}>
                  <Button
                    type="link"
                    aria-controls="execution-report"
                    onClick={() => showReport(c.case_id)}
                  >
                    查看尝试
                  </Button>
                  <Button type="link" href={`#/cases/${c.case_id}`}>
                    查看用例详情
                  </Button>
                </Space>
              ),
            },
          ]}
        />
      </section>
      {current && (
        <section
          id="execution-report"
          className="execution-report"
          aria-labelledby="execution-report-heading"
          ref={reportRef}
        >
          <div className="task-attempt-heading">
            <h2 id="execution-report-heading">
              {current.code} · {current.case_name}
            </h2>
            {attempt && (
              <Select
                aria-label="选择执行尝试"
                value={attempt.id}
                onChange={setAttemptId}
                options={current.attempts.map((a) => ({
                  value: a.id,
                  label: `第 ${a.number} 次 · ${verdicts[a.verdict] ?? a.verdict}${a.diagnostics?.incidents?.length ? ` · ${a.diagnostics?.incidents?.length ?? 0} 条稳定性事件` : ""}`,
                }))}
              />
            )}
            {attempt &&
              !run.task_id &&
              ["completed", "cancelled", "blocked"].includes(
                current.attempts[0].state,
              ) && (
                <Button disabled={busy || working} onClick={() => void retry()}>
                  重新运行此用例
                </Button>
              )}
          </div>
          {attempt ? (
            <AttemptView attempt={attempt} />
          ) : (
            <NoData text="该用例暂无执行尝试" />
          )}
        </section>
      )}
    </>
  );
}
function AttemptView({ attempt }: { attempt: TaskAttempt }) {
  const { data } = usePlatform();
  const device = data?.devices.find((d) => d.attempt_id === attempt.id);
  return (
    <>
      <div className="result-grid">
        <div>
          <span>执行状态</span>
          <Status state={attempt.state} />
        </div>
        <div>
          <span>断言结论</span>
          <strong>{verdicts[attempt.verdict]}</strong>
        </div>
        <div>
          <span>清理状态</span>
          <strong>{cleanupLabels[attempt.cleanup]}</strong>
        </div>
        <div>
          <span>证据完整性</span>
          <strong>{evidenceLabels[attempt.evidence]}</strong>
        </div>
      </div>
      <div className="run-detail-layout">
        <ExecutionEvidence key={attempt.id} attempt={attempt} />
        <div className="aside-column">
          <Panel title="尝试信息">
            <dl className="detail-facts">
              <div>
                <dt>尝试 ID</dt>
                <dd className="mono break-all">{attempt.id}</dd>
              </div>
              <div>
                <dt>执行版本</dt>
                <dd className="mono break-all">
                  {attempt.version_id ?? "尚未读取"}
                </dd>
              </div>
              <div>
                <dt>开始排队</dt>
                <dd>{time(attempt.created_at)}</dd>
              </div>
              <div>
                <dt>结束时间</dt>
                <dd>{attempt.finished_at ? time(attempt.finished_at) : "—"}</dd>
              </div>
              <div>
                <dt>当前设备占用</dt>
                <dd>
                  {device ? (
                    <a
                      href={`#/devices?node=${encodeURIComponent(device.node_id)}`}
                    >
                      {device.serial}
                    </a>
                  ) : (
                    "当前无设备占用"
                  )}
                </dd>
              </div>
            </dl>
          </Panel>
          <Panel title="结果说明">
            <div className="panel-body">
              <p className="body-note">
                {attempt.reason ===
                "Simulation only; no App/OS assertion executed"
                  ? "本次为模拟执行，未执行真实 App 或 Android OS 断言。"
                  : attempt.reason || "等待执行结果"}
              </p>
              {attempt.simulation && (
                <Alert
                  type="info"
                  showIcon
                  message="模拟执行"
                  description="用于验证调度、并发和重试，不代表真实设备通过。"
                />
              )}
            </div>
          </Panel>
        </div>
      </div>
      <DeviceDiagnosticsPanel attempt={attempt} />
    </>
  );
}

function ExecutionEvidence({ attempt }: { attempt: TaskAttempt }) {
  const [report, setReport] = useState<ExecutionReport | null>();
  const [error, setError] = useState("");
  useEffect(() => {
    if (attempt.simulation) return;
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const value = await api.executionReport(attempt.id, controller.signal);
        if (controller.signal.aborted) return;
        setError("");
        setReport(value);
        if (value && (value.visual_version || !value.session_status)) return;
      } catch (e) {
        if (controller.signal.aborted) return;
        setError(e instanceof Error ? e.message : "读取执行证据失败");
      }
      timer = setTimeout(() => void load(), 5000);
    };
    void load();
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [attempt.id, attempt.simulation]);
  return (
    <Panel
      title="执行步骤"
      description="查看 Artemis 每一步的操作、画面与判定。"
    >
      {attempt.simulation ? (
        <NoData text="本次为模拟执行，没有设备截图" />
      ) : error && !report ? (
        <div className="panel-body">
          <Alert type="error" message="执行步骤读取失败" description={error} />
        </div>
      ) : (
        <div className="panel-body execution-evidence">
          {report?.visual_version && report.steps?.length ? (
            <StepTimeline attemptId={attempt.id} steps={report.steps} />
          ) : (
            <NoData
              text={
                report === undefined
                  ? "正在读取执行步骤…"
                  : report?.startup_log && !report.session_status
                    ? "执行未启动，没有生成步骤截图"
                    : !attempt.finished_at
                      ? "等待节点同步步骤和截图"
                      : "本次尝试暂无可查看的步骤截图"
              }
            />
          )}
          {(report?.startup_log ||
            report?.case_result ||
            Boolean(report?.checks?.length)) && (
            <details className="execution-raw-evidence">
              <summary>原始报告与日志</summary>
              {report?.startup_log && <pre>{report.startup_log}</pre>}
              {report?.case_result && <pre>{report.case_result}</pre>}
              {Boolean(report?.checks?.length) && (
                <pre>{report?.checks?.join("\n")}</pre>
              )}
            </details>
          )}
          {report?.truncated && (
            <p className="body-note">部分证据未能读取或超过保存上限。</p>
          )}
        </div>
      )}
    </Panel>
  );
}

type ReportStep = NonNullable<ExecutionReport["steps"]>[number];
type StepFrame = { digest: string; label: string; paths?: ReportStep["paths"] };

const stepDecisions = {
  passed: { label: "通过", color: "green" },
  failed: { label: "未通过", color: "red" },
  skipped: { label: "已跳过", color: "default" },
  unchecked: { label: "未评估", color: "default" },
  inconclusive: { label: "结论不充分", color: "gold" },
};

function FrameImage({
  attemptId,
  frame,
}: {
  attemptId: string;
  frame: StepFrame;
}) {
  const [failed, setFailed] = useState(false);
  if (failed) return <div className="step-image-empty">截图无法加载</div>;
  return (
    <span className="step-frame-image">
      <img
        src={api.executionFrameUrl(attemptId, frame.digest)}
        alt={frame.label}
        onError={() => setFailed(true)}
      />
      {frame.paths?.length ? (
        <span className="step-annotations" aria-hidden="true">
          <svg viewBox="0 0 1000 1000" preserveAspectRatio="none">
            {frame.paths.map((path, index) => (
              <polyline
                key={index}
                points={path.map(([x, y]) => `${x},${y}`).join(" ")}
                fill="none"
                stroke="currentColor"
                strokeWidth="3"
                strokeLinecap="round"
                strokeLinejoin="round"
                vectorEffect="non-scaling-stroke"
              />
            ))}
          </svg>
          {frame.paths.map((path, index) => {
            const [x, y] = path[0];
            const [endX, endY] = path[path.length - 1];
            return (
              <span key={index}>
                <span
                  className="step-marker"
                  style={{ left: `${x / 10}%`, top: `${y / 10}%` }}
                />
                {path.length > 1 && (
                  <span
                    className="step-marker-end"
                    style={{ left: `${endX / 10}%`, top: `${endY / 10}%` }}
                  />
                )}
              </span>
            );
          })}
        </span>
      ) : null}
    </span>
  );
}

function StepTimeline({
  attemptId,
  steps,
}: {
  attemptId: string;
  steps: ReportStep[];
}) {
  const [page, setPage] = useState(1);
  const [preview, setPreview] = useState<StepFrame>();
  const pageSize = 4;
  return (
    <div className="execution-timeline">
      {steps.slice((page - 1) * pageSize, page * pageSize).map((step) => {
        const frames: StepFrame[] = [];
        if (step.before)
          frames.push({
            digest: step.before,
            label: "操作前",
            paths: step.paths,
          });
        if (step.after) frames.push({ digest: step.after, label: "操作后" });
        const decision = step.validation
          ? stepDecisions[step.validation]
          : undefined;
        const dispatchFailed = ["failed", "error", "dispatch_failed"].includes(
          step.status ?? "",
        );
        return (
          <article className="execution-step" key={step.number}>
            <header>
              <h3>{step.number === 0 ? "执行准备" : `第 ${step.number} 步`}</h3>
              {dispatchFailed && <Tag color="red">操作执行失败</Tag>}
              {decision ? (
                <Tag color={decision.color}>操作校验{decision.label}</Tag>
              ) : (
                step.number > 0 && <Tag>暂无操作判定</Tag>
              )}
            </header>
            <div className="execution-step-body">
              {frames.length ? (
                <div className="execution-step-frames">
                  {frames.map((frame) => (
                    <figure key={frame.label}>
                      <button
                        type="button"
                        onClick={() => setPreview(frame)}
                        aria-label={`放大第 ${step.number} 步${frame.label}截图`}
                      >
                        <FrameImage attemptId={attemptId} frame={frame} />
                      </button>
                      <figcaption>{frame.label}</figcaption>
                    </figure>
                  ))}
                </div>
              ) : step.number > 0 ? (
                <div className="step-image-empty">本步暂无截图</div>
              ) : null}
              <div className="execution-step-description">
                {step.instruction && (
                  <p>
                    <strong>操作</strong> {step.instruction}
                  </p>
                )}
                {step.observation && (
                  <p>
                    <strong>Artemis 观察</strong> {step.observation}
                  </p>
                )}
                {step.validation_note && (
                  <p>
                    <strong>校验说明</strong> {step.validation_note}
                  </p>
                )}
                {step.assertions?.map((check, index) => (
                  <div className="execution-step-check" key={index}>
                    <Tag color={stepDecisions[check.status].color}>
                      断言{stepDecisions[check.status].label}
                    </Tag>
                    <p>{check.criterion}</p>
                    {check.evidence && <p>{check.evidence}</p>}
                  </div>
                ))}
                {!step.instruction &&
                  !step.observation &&
                  step.number === 0 && <p>Artemis 准备执行计划。</p>}
              </div>
            </div>
          </article>
        );
      })}
      {steps.length > pageSize && (
        <Pagination
          current={page}
          onChange={setPage}
          pageSize={pageSize}
          showSizeChanger={false}
          total={steps.length}
        />
      )}
      <Modal
        open={Boolean(preview)}
        centered
        title={preview?.label}
        onCancel={() => setPreview(undefined)}
        footer={null}
        className="execution-frame-preview"
      >
        {preview && (
          <FrameImage
            key={preview.digest}
            attemptId={attemptId}
            frame={preview}
          />
        )}
      </Modal>
    </div>
  );
}

const captureStatus = {
  collecting: "采集中",
  complete: "采集完成",
  partial: "部分采集",
  unavailable: "采集不可用",
};

function DeviceDiagnosticsPanel({ attempt }: { attempt: TaskAttempt }) {
  const capture = attempt.diagnostics;
  const incidents = [...(capture?.incidents ?? [])].sort(
    (a, b) => Date.parse(a.occurred_at) - Date.parse(b.occurred_at),
  );
  const formatTime = (value: string) =>
    new Date(value).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      fractionalSecondDigits: 3,
      hour12: false,
    });
  return (
    <Panel
      title="稳定性事件 · ANR / Crash"
      description="记录本次执行期间设备 ActivityManager 报告的异常。发生时间按采集开始时的时钟偏差换算。"
    >
      <div className="panel-body">
        {attempt.simulation ? (
          <p className="body-note">模拟执行不采集设备事件。</p>
        ) : !capture ? (
          <p className="body-note">本次尚无采集记录。</p>
        ) : (
          <>
            <Tag
              color={
                capture.status === "partial" || capture.status === "unavailable"
                  ? "orange"
                  : "blue"
              }
            >
              {captureStatus[capture.status]}
            </Tag>
            <Tag>{capture.serial}</Tag>
            {capture.reason && <p className="body-note">{capture.reason}</p>}
            {capture.truncated && (
              <p className="body-note">事件超过保存上限，显示已保留的记录。</p>
            )}
            {incidents.length === 0 ? (
              <p className="body-note">
                {capture.status === "complete"
                  ? "采集窗口内未发现 ANR 或 Crash。"
                  : "当前没有已记录的事件。"}
              </p>
            ) : (
              <Table
                rowKey="id"
                size="small"
                scroll={{ x: 640 }}
                pagination={{ pageSize: 10, hideOnSinglePage: true }}
                dataSource={incidents}
                columns={[
                  {
                    title: "事件",
                    width: 96,
                    render: (_, incident) => (
                      <Tag color={incident.kind === "anr" ? "orange" : "red"}>
                        {incident.kind === "anr" ? "ANR" : "Crash"}
                      </Tag>
                    ),
                  },
                  {
                    title: "发生时间",
                    width: 174,
                    render: (_, incident) => (
                      <span
                        title={`设备时间 ${formatTime(incident.device_at)}`}
                      >
                        {formatTime(incident.occurred_at)}
                      </span>
                    ),
                  },
                  {
                    title: "进程 / 事件 PID",
                    width: 220,
                    render: (_, incident) => (
                      <span className="break-all">
                        {incident.process} · {incident.pid}
                      </span>
                    ),
                  },
                  {
                    title: "原因与日志",
                    render: (_, incident) => (
                      <details>
                        <summary className="break-all">
                          {incident.detail || "展开原始事件"}
                        </summary>
                        <pre
                          style={{
                            whiteSpace: "pre-wrap",
                            overflowWrap: "anywhere",
                          }}
                        >
                          {incident.raw}
                        </pre>
                      </details>
                    ),
                  },
                ]}
              />
            )}
          </>
        )}
      </div>
    </Panel>
  );
}
