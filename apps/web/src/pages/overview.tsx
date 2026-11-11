import { useEffect, useState } from "react";
import { api, type ExecutionBatch } from "@kraken/api-client";
import { BatchTable, batchActive } from "./tasks";
import { Alert, Button } from "antd";
import { usePlatform } from "../state";
import {
  Icon,
  Metric,
  Panel,
  PageHeading,
  Status,
  deviceStatus,
  online,
  time,
} from "../ui";
export function OverviewPage() {
  const { data, setTaskCase } = usePlatform();
  const [runs, setRuns] = useState<ExecutionBatch[]>([]);
  const [runError, setRunError] = useState("");
  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .executions()
        .then((value) => {
          if (alive) {
            setRuns(value);
            setRunError("");
          }
        })
        .catch((e) => {
          if (alive) setRunError(e.message);
        });
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);
  if (!data) return null;
  const active = runs.filter((r) => batchActive(r.state));
  const attention = runs.filter((r) =>
    ["blocked", "needs_recovery"].includes(r.state),
  );
  const ready = data.devices.filter(
    (d) => deviceStatus(d, data.nodes).state === "available",
  );
  const onlineDevices = data.devices.filter((d) =>
    d.adb_state
      ? ["observed", "available", "running"].includes(
          deviceStatus(d, data.nodes).state,
        )
      : online(data.nodes.find((n) => n.id === d.node_id)),
  );
  const stable = data.cases.filter((c) => c.stable_version_id);
  const activeNodes = [...data.nodes].sort(
    (a, b) => Number(online(b)) - Number(online(a)),
  );
  return (
    <>
      <PageHeading
        eyebrow="WORKSPACE OVERVIEW"
        title="测试总览"
        description="掌握任务进度与设备资源，从这里开始一次测试。"
        actions={
          <Button
            type="primary"
            icon={<Icon name="plus" />}
            onClick={() => setTaskCase("")}
          >
            新建测试任务
          </Button>
        }
      />
      <div className="environment-note">
        <span className="environment-dot" />
        <strong>测试环境</strong>
        <span>可在任务中选择模拟执行或指定真机。</span>
        <a href="#/settings">
          环境信息 <Icon name="arrow" size={14} />
        </a>
      </div>
      <div className="metrics-grid">
        <Metric
          title="进行中的批次"
          value={active.length}
          icon="runs"
          accent
          meta={`${runs.length} 个已载入批次 · 自动更新`}
        />
        <Metric
          title="在线设备"
          value={
            <>
              {onlineDevices.length}
              <small>/ {data.devices.length}</small>
            </>
          }
          icon="devices"
          meta={
            <>
              <span className="positive">{ready.length} 台可用</span>
              <span className="metric-divider">/</span>
              {data.devices.filter((d) => d.attempt_id).length} 台占用
            </>
          }
        />
        <Metric
          title="稳定用例"
          value={stable.length}
          icon="cases"
          meta={`共 ${data.cases.length} 个用例 · ${data.cases.length - stable.length} 个待设置 Stable`}
        />
        <Metric
          title="待处理异常"
          value={attention.length}
          icon="layers"
          meta={
            attention.length
              ? "存在阻塞或待恢复任务"
              : "当前任务记录中无阻塞或待恢复项"
          }
        />
      </div>
      <div className="overview-columns">
        <div className="main-column">
          <Panel
            title="最近执行批次"
            description="展示每个批次的整体进度与状态"
            extra={
              <a className="text-link" href="#/runs">
                全部任务 <Icon name="arrow" size={15} />
              </a>
            }
          >
            {runError && <Alert type="error" message={runError} />}
            <BatchTable items={runs.slice(0, 5)} />
            <div className="panel-footnote">
              <Icon name="clock" size={14} />
              已载入 {runs.length} 个执行批次，最多 100 条。
            </div>
          </Panel>
          <Panel
            title="开始测试"
            description="从用例到执行，集中管理你的测试工作"
            className="quick-start-panel"
          >
            <div className="quick-start-grid">
              <a href="#/cases/new">
                <span className="quick-icon">
                  <Icon name="cases" size={22} />
                </span>
                <strong>创建测试用例</strong>
                <span>编写操作步骤与断言，管理稳定版本</span>
                <Icon name="arrow" size={16} />
              </a>
              <a href="#/devices">
                <span className="quick-icon teal">
                  <Icon name="devices" size={22} />
                </span>
                <strong>检查设备资源</strong>
                <span>查看设备在线状态与任务占用</span>
                <Icon name="arrow" size={16} />
              </a>
              <a href="#/runs">
                <span className="quick-icon violet">
                  <Icon name="runs" size={22} />
                </span>
                <strong>跟进执行过程</strong>
                <span>查看尝试记录、内容快照与清理状态</span>
                <Icon name="arrow" size={16} />
              </a>
            </div>
          </Panel>
        </div>
        <div className="aside-column">
          <Panel
            title="节点资源"
            extra={
              <a className="text-link" href="#/nodes">
                查看 <Icon name="arrow" size={14} />
              </a>
            }
          >
            <div className="node-overview-summary">
              <strong>
                {data.nodes.filter(online).length}
                <span> / {data.nodes.length}</span>
              </strong>
              <span>节点在线</span>
            </div>
            <div className="node-capacities">
              {activeNodes.slice(0, 4).map((n) => {
                const devices = data.devices.filter((d) => d.node_id === n.id);
                const held = devices.filter((d) => d.attempt_id).length;
                return (
                  <a
                    className="node-capacity"
                    href={`#/devices?node=${encodeURIComponent(n.id)}`}
                    key={n.id}
                  >
                    <div>
                      <span>
                        <i
                          className={`health-dot ${online(n) ? "healthy" : ""}`}
                        />
                        {n.id}
                      </span>
                      <small>
                        {online(n)
                          ? `${held} / ${devices.length} 占用`
                          : "离线"}
                      </small>
                    </div>
                    <div className="capacity-track">
                      <i
                        style={{
                          width: `${devices.length ? (held / devices.length) * 100 : 0}%`,
                        }}
                      />
                    </div>
                  </a>
                );
              })}
              {!activeNodes.length && <p className="muted">尚无已注册节点</p>}
            </div>
          </Panel>
          <div className="version-note">
            <div className="note-icon">
              <Icon name="layers" size={22} />
            </div>
            <h3>稳定版本，明确执行</h3>
            <p>
              每次修改保留为新版本。任务开始时读取
              Stable，执行记录保存当次使用的内容。
            </p>
            <a className="text-link" href="#/cases">
              管理用例版本 <Icon name="arrow" size={15} />
            </a>
          </div>
        </div>
      </div>
    </>
  );
}
