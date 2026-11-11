import { usePlatform } from "../state";
import { Icon, PageHeading, Panel, Status, online, time } from "../ui";

export function SettingsPage() {
  const { data, error, updated } = usePlatform();
  return (
    <>
      <PageHeading
        eyebrow="PLATFORM SETTINGS"
        title="平台设置"
        description="查看平台的运行环境、访问模式与能力边界。"
      />
      <div className="settings-layout">
        <Panel title="平台环境" description="基础验证环境">
          <div className="settings-rows">
            <div>
              <span className="settings-row-icon">
                <Icon name="overview" />
              </span>
              <div>
                <strong>开放访问</strong>
                <p>当前为开放访问模式，无需登录。</p>
              </div>
              <Status state="available" label="已启用" />
            </div>
            <div>
              <span className="settings-row-icon">
                <Icon name="nodes" />
              </span>
              <div>
                <strong>控制面连接</strong>
                <p>
                  {error
                    ? "连接异常，请检查本地服务。"
                    : `最近同步 ${time(updated?.toISOString())} · ${data?.nodes.filter(online).length ?? 0} 个在线节点`}
                </p>
              </div>
              <Status
                state={error ? "blocked" : "available"}
                label={error ? "异常" : "已连接"}
              />
            </div>
            <div>
              <span className="settings-row-icon">
                <Icon name="runs" />
              </span>
              <div>
                <strong>执行环境</strong>
                <p>用于验证调度、独占、恢复与清理流程。</p>
              </div>
              <span className="neutral-tag">M0</span>
            </div>
          </div>
        </Panel>
        <Panel title="后续接入" description="以下能力尚未开放配置">
          <div className="settings-rows">
            {[
              {
                icon: "cases" as const,
                title: "用例导入与同步",
                text: "飞书普通电子表格手动和定时读取、预览与人工采纳。Excel 导入已在导入中心提供。",
              },
              {
                icon: "devices" as const,
                title: "设备控制与刷机",
                text: "远程交互、刷机包来源与执行链路。",
              },
              {
                icon: "layers" as const,
                title: "身份与权限",
                text: "通过独立权限模块扩展身份认证，可由部署方接入 LDAP 等目录服务。",
              },
            ].map((item) => (
              <div key={item.title}>
                <span className="settings-row-icon">
                  <Icon name={item.icon} />
                </span>
                <div>
                  <strong>{item.title}</strong>
                  <p>{item.text}</p>
                </div>
                <span className="neutral-tag">待接入</span>
              </div>
            ))}
          </div>
        </Panel>
      </div>
    </>
  );
}
