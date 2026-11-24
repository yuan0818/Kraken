import { useState } from "react";
import { RemoteControl } from "../remote-control";
import { Button, Input, Modal, Segmented, Select, Table } from "antd";
import { usePlatform } from "../state";
import {
  Back,
  Icon,
  NoData,
  PageHeading,
  Status,
  deviceStatus,
  online,
  shortId,
  time,
  type Device,
} from "../ui";

export function DevicesPage() {
  const { data, route } = usePlatform();
  const [search, setSearch] = useState("");
  const [node, setNode] = useState(route.node ?? "all");
  const [state, setState] = useState("all");
  const [view, setView] = useState("grid");
  const [selected, setSelected] = useState<string>();
  if (!data) return null;
  const devices = data.devices.filter(
    (d) =>
      `${d.serial} ${d.id} ${d.node_id} ${d.tags.join(" ")}`
        .toLowerCase()
        .includes(search.toLowerCase()) &&
      (node === "all" || d.node_id === node) &&
      (state === "all" || deviceStatus(d, data.nodes).state === state),
  );
  const current = data.devices.find((d) => d.id === selected);
  const held =
    current && data.attempts.find((a) => a.id === current.attempt_id);
  return (
    <>
      <PageHeading
        eyebrow="DEVICE INVENTORY"
        title="设备中心"
        description="查看连接到各节点的设备，统一掌握可用资源与独占状态。"
        actions={
          <span className="heading-meta">
            <i className="health-dot healthy" />
            自动同步节点状态
          </span>
        }
      />
      <div className="summary-strip">
        <span>
          已注册设备 <strong>{data.devices.length}</strong>
        </span>
        {[
          { state: "available", label: "可用" },
          { state: "observed", label: "仅监测" },
          { state: "running", label: "占用" },
          { state: "needs_recovery", label: "隔离" },
          { state: "offline", label: "未就绪 / 离线" },
        ].map((s) => (
          <span key={s.state}>
            <Status state={s.state} label={s.label} />
            <strong>
              {
                data.devices.filter(
                  (d) => deviceStatus(d, data.nodes).state === s.state,
                ).length
              }
            </strong>
          </span>
        ))}
      </div>
      <section className="panel">
        <div className="filter-bar">
          <Input
            aria-label="搜索设备"
            prefix={<Icon name="search" size={17} />}
            placeholder="搜索 SN、节点或标签"
            className="search-input"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            allowClear
          />
          <Select
            aria-label="节点筛选"
            value={node}
            onChange={setNode}
            options={[
              { value: "all", label: "全部节点" },
              ...data.nodes.map((n) => ({ value: n.id, label: n.id })),
            ]}
          />
          <Select
            aria-label="设备状态筛选"
            value={state}
            onChange={setState}
            options={[
              { value: "all", label: "全部状态" },
              { value: "available", label: "可用" },
              { value: "observed", label: "在线 · 仅监测" },
              { value: "running", label: "占用" },
              { value: "needs_recovery", label: "隔离" },
              { value: "offline", label: "未就绪 / 离线" },
            ]}
          />
          <Segmented
            className="view-toggle"
            aria-label="设备视图"
            value={view}
            onChange={setView}
            options={[
              {
                value: "grid",
                label: (
                  <span className="icon-label">
                    <Icon name="overview" size={15} />
                    卡片
                  </span>
                ),
              },
              {
                value: "list",
                label: (
                  <span className="icon-label">
                    <Icon name="menu" size={15} />
                    列表
                  </span>
                ),
              },
            ]}
          />
        </div>
        {view === "grid" ? (
          <div className="device-grid">
            {devices.map((d) => {
              const status = deviceStatus(d, data.nodes);
              return (
                <button
                  className={`device-card ${status.state === "running" ? "occupied" : ""}`}
                  key={d.id}
                  onClick={() => setSelected(d.id)}
                >
                  <div className="device-card-top">
                    <span className="device-icon">
                      <Icon name="devices" size={27} />
                    </span>
                    <Status {...status} />
                  </div>
                  <h3 className="mono">{d.serial}</h3>
                  <p>
                    <Icon name="nodes" size={14} />
                    {d.node_id}
                  </p>
                  <div className="device-tags">
                    {d.tags.map((t) => (
                      <span className="neutral-tag" key={t}>
                        {t}
                      </span>
                    ))}
                  </div>
                  <div className="device-card-bottom">
                    <span>
                      {d.attempt_id ? "查看占用信息" : "查看设备详情"}
                    </span>
                    <Icon name="arrow" size={15} />
                  </div>
                </button>
              );
            })}
            {!devices.length && <NoData text="没有符合条件的设备" />}
          </div>
        ) : (
          <Table<Device>
            className="platform-table"
            rowKey="id"
            dataSource={devices}
            pagination={{
              pageSize: 20,
              hideOnSinglePage: true,
              showSizeChanger: false,
            }}
            scroll={{ x: 740 }}
            columns={[
              {
                title: "设备 SN",
                render: (_, d) => (
                  <Button
                    type="link"
                    className="table-link mono"
                    onClick={() => setSelected(d.id)}
                  >
                    {d.serial}
                  </Button>
                ),
              },
              { title: "连接节点", dataIndex: "node_id" },
              {
                title: "当前状态",
                render: (_, d) => <Status {...deviceStatus(d, data.nodes)} />,
              },
              {
                title: "标签",
                render: (_, d) => (
                  <div className="tags">
                    {d.tags.map((t) => (
                      <span className="neutral-tag" key={t}>
                        {t}
                      </span>
                    ))}
                  </div>
                ),
              },
              {
                title: "操作",
                render: (_, d) => (
                  <Button
                    type="link"
                    size="small"
                    onClick={() => setSelected(d.id)}
                  >
                    详情
                  </Button>
                ),
              },
            ]}
          />
        )}
        <div className="panel-footnote">
          <Icon name="devices" size={15} />
          {devices.length} 台设备 · 执行期间设备独占；节点离线不代表占用已释放。
        </div>
      </section>
      <Modal
        open={!!current}
        centered
        onCancel={() => setSelected(undefined)}
        title="设备详情"
        width={520}
        footer={null}
      >
        {current && (
          <>
            <div className="device-detail-heading">
              <span className="device-icon">
                <Icon name="devices" size={30} />
              </span>
              <div>
                <h2 className="mono">{current.serial}</h2>
                <Status {...deviceStatus(current, data.nodes)} />
              </div>
            </div>
            <dl className="detail-facts no-padding">
              {current.adb_state && (
                <>
                  <div>
                    <dt>ADB 型号 / 产品</dt>
                    <dd>
                      {current.adb_model ?? "—"} / {current.adb_product ?? "—"}
                    </dd>
                  </div>
                  <div>
                    <dt>最近采集</dt>
                    <dd>{time(current.observed_at)}</dd>
                  </div>
                </>
              )}
              <div>
                <dt>设备标识</dt>
                <dd className="mono break-all">{current.id}</dd>
              </div>
              <div>
                <dt>所属节点</dt>
                <dd>
                  <a href="#/nodes">{current.node_id}</a>
                </dd>
              </div>
              <div>
                <dt>当前占用</dt>
                <dd>
                  {current.attempt_id ? (
                    held ? (
                      <a
                        href={`#/executions/${held.run_id}`}
                        onClick={() => setSelected(undefined)}
                      >
                        {held.case_name} · 第 {held.number} 次尝试
                      </a>
                    ) : (
                      <span className="break-all">{current.attempt_id}</span>
                    )
                  ) : (
                    "无执行占用"
                  )}
                </dd>
              </div>
              <div>
                <dt>设备标签</dt>
                <dd className="tags">
                  {current.tags.map((t) => (
                    <span className="neutral-tag" key={t}>
                      {t}
                    </span>
                  ))}
                </dd>
              </div>
              <div>
                <dt>租约代次</dt>
                <dd>{current.generation}</dd>
              </div>
            </dl>
            {current.adb_state && (
              <Button
                type="primary"
                href={`#/devices/${current.id}/remote?node=${encodeURIComponent(node)}`}
              >
                打开远程手机
              </Button>
            )}
            <div className="subtle-note">
              <Icon name="help" size={18} />
              <p>打开远程手机观看画面，空闲时可申请独占控制权。</p>
            </div>
          </>
        )}
      </Modal>
    </>
  );
}

export function RemoteDevicePage({ id }: { id: string }) {
  const { data, route } = usePlatform();
  const device = data?.devices.find((item) => item.id === id);
  const node = route.node ?? device?.node_id;
  return (
    <>
      <Back
        href={
          node && node !== "all"
            ? `#/devices?node=${encodeURIComponent(node)}`
            : "#/devices"
        }
        label="返回设备中心"
      />
      <PageHeading
        title="远程手机"
        description={
          device
            ? `${device.serial} · ${device.node_id}`
            : "查看和操作已连接的设备。"
        }
      />
      {device?.adb_state ? (
        <section className="panel remote-device-panel">
          <RemoteControl id={device.id} serial={device.serial} />
        </section>
      ) : (
        <NoData text={device ? "该设备尚无 ADB 状态" : "未找到该设备"} />
      )}
    </>
  );
}

export function NodesPage() {
  const { data } = usePlatform();
  const [search, setSearch] = useState("");
  if (!data) return null;
  const nodes = data.nodes
    .filter((n) => n.id.toLowerCase().includes(search.toLowerCase()))
    .sort((a, b) => Number(online(b)) - Number(online(a)));
  return (
    <>
      <PageHeading
        eyebrow="EXECUTION RESOURCES"
        title="执行节点"
        description="统一管理执行节点，查看连接健康状态与设备分布。"
        actions={
          <span className="heading-meta">
            {data.nodes.filter(online).length} / {data.nodes.length} 个节点在线
          </span>
        }
      />
      <section className="panel">
        <div className="filter-bar">
          <Input
            aria-label="搜索节点"
            className="search-input"
            prefix={<Icon name="search" size={17} />}
            placeholder="搜索节点名称"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            allowClear
          />
          <span className="filter-summary">{nodes.length} 个节点</span>
        </div>
        <div className="nodes-grid">
          {nodes.map((n) => {
            const devices = data.devices.filter((d) => d.node_id === n.id);
            const held = devices.filter((d) => d.attempt_id).length;
            const available = devices.filter(
              (d) => deviceStatus(d, data.nodes).state === "available",
            ).length;
            return (
              <article className="node-card" key={n.id}>
                <div className="node-card-header">
                  <span className="node-icon">
                    <Icon name="nodes" size={23} />
                  </span>
                  <div>
                    <h2>{n.id}</h2>
                    <p>
                      {n.capabilities.includes("adb_inventory")
                        ? "ADB 采集节点"
                        : "执行节点"}
                    </p>
                  </div>
                  <Status
                    state={online(n) ? "available" : "offline"}
                    label={online(n) ? "在线" : "离线"}
                  />
                </div>
                <div className="node-stats">
                  <div>
                    <strong>{devices.length}</strong>
                    <span>已注册设备</span>
                  </div>
                  <div>
                    <strong>{held}</strong>
                    <span>当前占用</span>
                  </div>
                  <div>
                    <strong>{available}</strong>
                    <span>当前可用</span>
                  </div>
                </div>
                <div className="node-load-label">
                  <span>设备占用</span>
                  <span>
                    {held} / {devices.length}
                  </span>
                </div>
                <div className="capacity-track">
                  <i
                    style={{
                      width: `${devices.length ? (held / devices.length) * 100 : 0}%`,
                    }}
                  />
                </div>
                <dl className="node-facts">
                  <div>
                    <dt>最近心跳</dt>
                    <dd>{time(n.last_seen)}</dd>
                  </div>
                  <div>
                    <dt>已声明能力</dt>
                    <dd className="tags">
                      {n.capabilities.length
                        ? n.capabilities.map((c) => (
                            <span className="neutral-tag" key={c}>
                              {c}
                            </span>
                          ))
                        : "未声明"}
                    </dd>
                  </div>
                </dl>
                <a
                  className="node-card-footer"
                  href={`#/devices?node=${encodeURIComponent(n.id)}`}
                >
                  查看节点设备 <Icon name="arrow" size={16} />
                </a>
              </article>
            );
          })}
          {!nodes.length && <NoData text="暂无符合条件的节点" />}
        </div>
      </section>
    </>
  );
}
