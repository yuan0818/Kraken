import { useEffect, useState } from "react";
import { Alert, Button, Drawer, Skeleton, Tooltip } from "antd";
import { navTitles, usePlatform, type Section } from "./state";
import { Icon, type IconName } from "./ui";
import { CatalogsPage } from "./pages/catalogs";
import { CaseEditorPage } from "./editors";
import { OverviewPage } from "./pages/overview";
import { CasesPage, CaseDetailPage } from "./pages/cases";
import { ImportsPage } from "./pages/imports";
import { ExecutionPage } from "./pages/execution";
import { TasksPage, TaskDetailPage, TaskCreator } from "./pages/tasks";
import { DevicesPage, NodesPage, RemoteDevicePage } from "./pages/resources";
import { SettingsPage } from "./pages/settings";
import { ReportDetail, ReportsPage } from "./pages/reports";
import { ModelEditorPage, ModelHistoryPage, ModelsPage } from "./pages/models";

const groups: {
  title: string;
  items: { key: Section; icon: IconName }[];
}[] = [
  {
    title: "测试管理",
    items: [
      { key: "overview", icon: "overview" },
      { key: "cases", icon: "cases" },
      { key: "imports", icon: "layers" },
      { key: "runs", icon: "runs" },
      { key: "reports", icon: "reports" },
    ],
  },
  {
    title: "资源与调度",
    items: [
      { key: "devices", icon: "devices" },
      { key: "nodes", icon: "nodes" },
      { key: "schedule", icon: "schedule" },
    ],
  },
  {
    title: "管理",
    items: [
      { key: "catalogs", icon: "layers" },
      { key: "models", icon: "settings" },
      { key: "settings", icon: "settings" },
    ],
  },
];
function Navigation({
  compact = false,
  onNavigate,
}: {
  compact?: boolean;
  onNavigate?: () => void;
}) {
  const { route, data } = usePlatform();
  return (
    <>
      <a
        className="brand"
        href="#/overview"
        aria-label="Kraken 平台首页"
        onClick={onNavigate}
      >
        <span className="brand-mark">
          <svg
            width="24"
            height="25"
            viewBox="0 0 24 25"
            fill="none"
            aria-hidden="true"
          >
            <path
              d="M4 4h4v7l8-7h6L12 13l10 9h-6l-8-7v7H4V4Z"
              fill="currentColor"
            />
          </svg>
        </span>
        <span className="brand-name">
          Kraken<span>TEST PLATFORM</span>
        </span>
      </a>
      <nav className="sidebar-navigation" aria-label="主导航">
        {groups.map((group) => (
          <div className="nav-group" key={group.title}>
            <div className="nav-group-title">{group.title}</div>
            {group.items.map((item) => (
              <a
                href={`#/${item.key}`}
                className={`nav-item ${route.section === item.key || (route.section === "executions" && item.key === "runs") ? "active" : ""}`}
                aria-current={
                  route.section === item.key ||
                  (route.section === "executions" && item.key === "runs")
                    ? "page"
                    : undefined
                }
                key={item.key}
                title={compact ? navTitles[item.key] : undefined}
                onClick={onNavigate}
              >
                <Icon name={item.icon} />
                <span>{navTitles[item.key]}</span>
                {item.key === "devices" && !!data?.devices.length ? (
                  <small className="nav-count">{data.devices.length}</small>
                ) : null}
              </a>
            ))}
          </div>
        ))}
      </nav>
      <div className="sidebar-bottom">
        <div className="sidebar-footer">
          <span>Kraken</span>
          <span>v0.1</span>
        </div>
      </div>
    </>
  );
}
export function Platform() {
  const { route, data, error, loading, updated, refresh } = usePlatform();
  const [compact, setCompact] = useState(false);
  const [mobile, setMobile] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const routeLabel =
    route.id === "new"
      ? "新建"
      : route.view === "edit"
        ? "编辑"
        : route.view === "history"
          ? "历史版本"
          : route.view === "remote"
            ? "远程手机"
            : "详情";
  useEffect(() => {
    document.title = `${navTitles[route.section]} · Kraken`;
  }, [route.section]);
  const refreshNow = async () => {
    setRefreshing(true);
    await refresh();
    setRefreshing(false);
  };
  const page = () => {
    switch (route.section) {
      case "imports":
        return <ImportsPage />;
      case "cases":
        if (route.id === "new" || (route.id && route.view === "edit"))
          return <CaseEditorPage />;
        return route.id ? (
          <CaseDetailPage key={route.id} id={route.id} />
        ) : (
          <CasesPage />
        );
      case "runs":
        return route.id ? (
          <TaskDetailPage key={route.id} id={route.id} />
        ) : (
          <TasksPage />
        );
      case "devices":
        if (route.id && route.view === "remote")
          return <RemoteDevicePage key={route.id} id={route.id} />;
        return <DevicesPage key={route.node ?? "all"} />;
      case "nodes":
        return <NodesPage />;
      case "executions":
        return route.id ? (
          <ExecutionPage key={`${route.id}?${route.query}`} id={route.id} />
        ) : (
          <TasksPage />
        );
      case "schedule":
        return <TasksPage key="scheduled" scheduleOnly />;
      case "reports":
        return route.id ? (
          <ReportDetail key={route.id} id={route.id} />
        ) : (
          <ReportsPage />
        );
      case "catalogs":
        return <CatalogsPage />;
      case "settings":
        return <SettingsPage />;
      case "models":
        if (route.id === "new") return <ModelEditorPage key="new" />;
        if (route.id && route.view === "edit")
          return <ModelEditorPage key={route.id} id={route.id} />;
        if (route.id && route.view === "history")
          return <ModelHistoryPage key={route.id} id={route.id} />;
        return <ModelsPage />;
      default:
        return <OverviewPage />;
    }
  };
  return (
    <div className={`app-shell ${compact ? "sidebar-compact" : ""}`}>
      <a
        className="skip-link"
        href="#main-content"
        onClick={(e) => {
          e.preventDefault();
          document.getElementById("main-content")?.focus();
        }}
      >
        跳至内容
      </a>
      <aside className="sidebar">
        <Navigation compact={compact} />
        <button
          className="sidebar-collapse"
          onClick={() => setCompact(!compact)}
          aria-label={compact ? "展开侧栏" : "收起侧栏"}
        >
          <Icon name={compact ? "chevron" : "back"} size={15} />
        </button>
      </aside>
      <div className="app-main">
        <header className="topbar">
          <div className="breadcrumbs">
            <Button
              className="mobile-menu"
              type="text"
              icon={<Icon name="menu" />}
              aria-label="打开导航"
              onClick={() => setMobile(true)}
            />
            <a href="#/overview">Kraken</a>
            <Icon name="chevron" size={13} />
            <span>{navTitles[route.section]}</span>
            {route.id && (
              <>
                <Icon name="chevron" size={13} />
                <strong>{routeLabel}</strong>
              </>
            )}
          </div>
          <div className="topbar-tools">
            <span
              className={`sync-state ${error ? "disconnected" : ""}`}
              title={
                updated
                  ? `最近同步：${updated.toLocaleTimeString("zh-CN", { hour12: false })}`
                  : "正在连接"
              }
            >
              <i
                className={`health-dot ${error ? "amber" : loading ? "" : "healthy"}`}
              />
              {error ? "连接中断" : loading ? "连接中" : "已同步"}
            </span>
            <Tooltip title="刷新平台状态">
              <Button
                type="text"
                aria-label="刷新平台状态"
                loading={refreshing}
                icon={<Icon name="refresh" size={17} />}
                onClick={() => void refreshNow()}
              />
            </Tooltip>
            <span className="topbar-divider" />
            <span className="access-mode">开放模式</span>
            <a
              className="profile-avatar"
              href="#/settings"
              aria-label="平台设置"
            >
              <Icon name="settings" size={16} />
            </a>
          </div>
        </header>
        <main id="main-content" tabIndex={-1} className="page-content">
          {error && (
            <Alert
              className="connection-alert"
              type="error"
              showIcon
              message="控制面连接异常"
              description={`显示上次成功同步的内容，写入操作暂不可用。${error}`}
              action={
                <Button size="small" onClick={() => void refreshNow()}>
                  重试
                </Button>
              }
            />
          )}
          {loading ? (
            <div className="loading-layout">
              <Skeleton active />
              <Skeleton active />
              <Skeleton active />
            </div>
          ) : data ? (
            page()
          ) : (
            <div className="panel connection-empty">
              <Icon name="nodes" size={32} />
              <h2>等待连接控制面</h2>
              <p>请检查平台服务，连接后即可加载平台数据。</p>
              <Button type="primary" onClick={() => void refreshNow()}>
                重新连接
              </Button>
            </div>
          )}
          <footer className="page-footer">
            <span>Kraken</span>
            <span>用例 · 设备 · 执行</span>
          </footer>
        </main>
      </div>
      <Drawer
        open={mobile}
        placement="left"
        onClose={() => setMobile(false)}
        width={260}
        styles={{ body: { padding: 0 }, header: { padding: "10px 16px" } }}
        title="平台导航"
      >
        <div className="mobile-sidebar">
          <Navigation onNavigate={() => setMobile(false)} />
        </div>
      </Drawer>
      <TaskCreator />
    </div>
  );
}
