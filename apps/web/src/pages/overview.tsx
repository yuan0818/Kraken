import { useEffect, useState } from "react";
import { api, type Dashboard } from "@kraken/api-client";
import { Alert, Button, Empty, Select, Spin, Table } from "antd";
import { TaskHistoryFilter, type HistoryTask } from "./tasks";
import { usePlatform } from "../state";
import { Metric, Panel, PageHeading, Status, deviceStatus, online, time, verdicts, engineText } from "../ui";

const colors: Record<string, string> = {
  pass: "#28a58e", fail: "#e46564", blocked: "#e6a23c",
  inconclusive: "#8b78cd", not_evaluated: "#b8c3d8",
};

export function OverviewPage() {
  const { data, setTaskCase } = usePlatform();
  const [days, setDays] = useState(7);
  const [mode, setMode] = useState("physical");
  const [task, setTask] = useState<HistoryTask>();
  const [summary, setSummary] = useState<Dashboard>();
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    setSummary(undefined);
    setError("");
    const load = async () => {
      try {
        const query = new URLSearchParams({ days: String(days), mode });
        if (task) query.set("task_id", task.value);
        const result = await api.dashboard(query.toString(), controller.signal);
        if (!controller.signal.aborted) { setSummary(result); setError(""); }
      } catch (e) {
        if (!controller.signal.aborted) setError(e instanceof Error ? e.message : "加载看板失败");
      } finally {
        if (!controller.signal.aborted) timer = setTimeout(load, 15000);
      }
    };
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [days, mode, task?.value, refresh]);
  const active = summary ? ["queued", "running", "retry_wait", "cancel_pending"].reduce((n, k) => n + (summary.batch_states[k] || 0), 0) : 0;
  const maxDaily = Math.max(1, ...summary?.trend.map(d => d.total) || []);
  const ready = data?.devices.filter(d => deviceStatus(d, data.nodes).state === "available").length || 0;
  return <>
    <PageHeading eyebrow="QUALITY DASHBOARD" title="测试看板"
      description="查看测试结果、执行趋势和资源状态，跟进需要处理的问题。"
      actions={<><Button onClick={() => setRefresh(v => v + 1)}>刷新看板</Button><Button type="primary" onClick={() => setTaskCase("")}>新建测试任务</Button></>} />
    <div className="dashboard-filters">
      <Select aria-label="统计时间范围" value={days} onChange={setDays} options={[7, 30, 90].map(value => ({ value, label: `最近 ${value} 天` }))} />
      <Select aria-label="执行类型" value={mode} onChange={setMode} options={[{ value: "physical", label: "真机执行" }, { value: "simulation", label: "模拟执行" }, { value: "all", label: "全部执行" }]} />
      <TaskHistoryFilter task={task} onTaskChange={setTask} label="按任务筛选看板" />
      <span className="muted">{summary ? `更新于 ${time(summary.generated_at)} · 每 15 秒刷新` : "正在读取统计"}</span>
    </div>
    {error && <Alert showIcon type="error" message="看板更新失败" description={`${engineText(error)}${summary ? "；当前保留上次成功的统计。" : "；请稍后刷新重试。"}`} />}
    {!summary && !error && <div className="dashboard-loading"><Spin tip="加载看板"><div style={{ height: 120 }} /></Spin></div>}
    {summary && <>
      <div className="dashboard-caption">{summary.start_date} 起创建的批次 · Asia/Taipei · 每个批次的每条用例按最新尝试计数，重试不重复计入用例数。</div>
      {summary.batches === 0 && <Alert showIcon type="info" message="此范围暂无执行记录" description="可切换时间、执行类型或任务筛选；下方资源状态仍显示当前情况。" />}
      <div className="metrics-grid dashboard-metrics">
        <Metric title="用例执行数" value={summary.cases} icon="cases" accent meta={`${summary.batches} 个批次 · ${summary.attempts} 次尝试`} />
        <Metric title="通过率" value={summary.pass_rate === null ? "—" : `${summary.pass_rate}%`} icon="reports" meta={`${summary.verdicts.pass} / ${summary.evaluated} 条已有结论 · 首次通过 ${summary.first_pass}`} />
        <Metric title="进行中的批次" value={active} icon="runs" meta={`排队 ${summary.batch_states.queued || 0} · 执行 ${summary.batch_states.running || 0} · 等待重试 ${summary.batch_states.retry_wait || 0}`} />
        <Metric title="待恢复 / 阻塞批次" value={`${summary.batch_states.needs_recovery || 0} / ${summary.batch_states.blocked || 0}`} icon="layers" meta={`${summary.retried} 条用例发生过重试`} />
      </div>
      <div className="dashboard-grid">
        <Panel title="每日执行趋势" description="按批次创建日期归组，显示当前最新结果；柱高为用例数。绿色部分为通过数。">
          <div className="dashboard-chart-scroll">
            <div className="dashboard-chart" style={{ minWidth: Math.max(400, days * 34) }}>
              {summary.trend.map(day => <div className="dashboard-day" key={day.date} tabIndex={0} aria-label={`${day.date}：共 ${day.total} 条，通过 ${day.passed} 条，已有结论 ${day.evaluated} 条`} title={`${day.date} · 共 ${day.total} · 通过 ${day.passed} · 通过率 ${day.evaluated ? `${Math.round(day.passed / day.evaluated * 100)}%` : "—"}`}>
                <span>{day.total}</span>
                <div className="dashboard-bar-slot"><div className="dashboard-bar" style={{ height: `${day.total / maxDaily * 100}%` }}><i style={{ height: `${day.total ? day.passed / day.total * 100 : 0}%` }} /></div></div>
                <small>{day.date.slice(5)}</small>
              </div>)}
            </div>
          </div>
          <div className="dashboard-caption">通过率 = 通过 ÷ 已有结论（通过、失败、阻塞、结论不充分）。尚未评估的用例不参与分母。</div>
        </Panel>
        <Panel title="执行结论分布" description="最新尝试的结论，运行中的批次仍可能变化。">
          <div className="dashboard-outcomes">{Object.entries(summary.verdicts).map(([key, count]) => <div className="dashboard-outcome" key={key}>
            <div><span><i style={{ background: colors[key] }} />{verdicts[key]}</span><strong>{count}</strong></div>
            <div className="dashboard-track"><i style={{ background: colors[key], width: `${summary.cases ? count / summary.cases * 100 : 0}%` }} /></div>
          </div>)}</div>
        </Panel>
      </div>
      <div className="dashboard-grid">
        <Panel title="需要关注的执行" description="按批次从新到旧展示最多 10 项，点击查看具体尝试和证据。">
          {summary.issues.length ? <div className="dashboard-issues">{summary.issues.map((issue, index) => <a key={`${issue.run_id}-${index}`} className="dashboard-issue" href={issue.attempt_id ? `#/executions/${issue.run_id}?case=${issue.case_id}&attempt=${issue.attempt_id}` : `#/reports/${issue.run_id}`}>
            <div><strong>{engineText(issue.name)}</strong><Status state={issue.state} /></div>
            <span>{verdicts[issue.verdict]} · {engineText(issue.reason)}</span>
          </a>)}</div> : <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="此范围暂无需要关注的执行" />}
        </Panel>
        <Panel title="稳定性事件" description="所选批次所有尝试采集到的事件，包含重试期间。">
          <div className="dashboard-stability"><div><strong>{summary.anr}</strong><span>ANR</span></div><div><strong>{summary.crash}</strong><span>Crash</span></div></div>
          <div className="dashboard-caption">{summary.diagnostics_total ? `真机尝试采集完成 ${summary.diagnostics_complete} / ${summary.diagnostics_total}；未完成采集不代表没有异常。` : "此范围无真机采集记录。"}{summary.diagnostics_truncated > 0 && ` ${summary.diagnostics_truncated} 次采集达到事件上限，实际事件可能更多。`}</div>
          <a className="text-link" href="#/reports">查看报告中的事件详情 →</a>
        </Panel>
      </div>
      <Panel title="最近执行批次" description="展示符合当前筛选条件的最近 8 个批次。" extra={<a href="#/reports">全部测试报告 →</a>}>
        <Table dataSource={summary.recent} rowKey="id" pagination={false} scroll={{ x: 650 }} columns={[
          { title: "任务 / 批次", key: "name", render: (_, r) => <a href={`#/reports/${r.id}`}>{engineText(r.name)}<div className="muted">RUN-{r.id.slice(0, 8).toUpperCase()}</div></a> },
          { title: "创建时间", dataIndex: "created_at", render: time },
          { title: "状态", dataIndex: "state", render: (state: string) => <Status state={state} /> },
          { title: "通过 / 用例数", key: "results", render: (_, r) => `${r.passed} / ${r.total}` },
          { title: "操作", key: "action", render: (_, r) => <a href={`#/reports/${r.id}`}>查看报告</a> },
        ]} />
      </Panel>
    </>}
    {data && <Panel title="实时资源" description="当前全部设备与节点，不受上方时间、任务和执行类型筛选影响。" extra={<a href="#/devices">设备中心 →</a>}>
      <div className="dashboard-resource-summary"><span>在线节点 <strong>{data.nodes.filter(online).length} / {data.nodes.length}</strong></span><span>可调度设备 <strong>{ready} / {data.devices.length}</strong></span><span>稳定用例 <strong>{data.cases.filter(c => c.stable_version_id).length} / {data.cases.length}</strong></span></div>
      <div className="dashboard-resources">{data.nodes.map(node => {
        const devices = data.devices.filter(d => d.node_id === node.id);
        const available = devices.filter(d => deviceStatus(d, data.nodes).state === "available").length;
        return <a href={`#/devices?node=${encodeURIComponent(node.id)}`} className="dashboard-resource" key={node.id}><strong><i className={`health-dot ${online(node) ? "healthy" : ""}`} />{node.id}</strong><span>{online(node) ? "在线" : "离线"} · {available} 台可用 / {devices.length} 台设备</span></a>;
      })}</div>
    </Panel>}
  </>;
}
