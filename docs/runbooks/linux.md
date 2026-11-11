# Linux 验证与部署边界

macOS 开发机可先运行独立的 [Podman 多节点实验室](podman-lab.md)，验证 Linux ARM64 用户态、HTTPS/WSS、多节点模拟调度、持久日志与容器故障恢复。正式 systemd 与硬件验收仍按本文执行。

在直连手机的 Mac 或 Linux 主机上，可按 SN 登记并监测真机。控制面升级到最新迁移 `0014` 后，给采集进程配置独立的 `NODE_ID` 和 `API_URL`。节点支持内网 HTTP 或 HTTPS，无需节点 Token。运行以下命令，每 3 秒采集一次 ADB 清单，仅上报指定 SN。

```sh
.venv/bin/python -m node_agent.devices.observe --serial <设备SN>
```

设备中心显示型号、产品、最近采集时间和授权状态。在线真机标记为“在线 · 仅监测”，不分配模拟测试任务。断开手机后保留记录。采集进程停止后状态变为“采集已中断”。

控制面模板为 `deploy/compose/control-plane.yaml`，使用 PostgreSQL/RabbitMQ 持久卷、独立 API/scheduler/publisher/worker、一次性迁移和 Caddy TLS。先配置 `.env` 的 `POSTGRES_PASSWORD`、`RABBITMQ_PASSWORD`、`PLATFORM_HOST`、`PLATFORM_SECRET_KEY_HOST_FILE`。主密钥使用绝对宿主机路径，按 [模型配置说明](model-configurations.md) 生成并保留，权限 0600 且容器 UID 10001 可读；报告导出目录须由该 UID 可写。URL 中的密码必须使用 URI 安全字符或正确 percent-encode。内网自签 CA 需安装到浏览器和节点信任库。模板未在真实 Linux 控制机验收，不应直接当成完成上线。

```sh
docker compose --env-file .env -f deploy/compose/control-plane.yaml config
docker compose --env-file .env -f deploy/compose/control-plane.yaml up -d --build
```

迁移前备份；回退需数据库恢复方案，不能仅回退镜像。单机 Compose 不提供主机故障自动切换。NAS 上传/校验/留存尚未实现，现阶段无证据清理作业。

节点安装到 `/opt/kraken`，独立 Python 环境；用户/组 `kraken`，状态目录 `/var/lib/kraken`，配置 `/etc/kraken/node.env`。当前 `kraken-node.service` 运行单进程模拟 runtime，包含通信与监督，不再启动独立 supervisor。真机使用 `node_agent.supervisor.artemis --serial <SN>`；升级旧节点见 [节点运行说明](node-runtime.md)。

先运行 `scripts/linux-preflight.sh`。该检查只读取工具版本与校验 unit 配置，不会操作手机。真实部署还需分别验证：节点注册/WSS信任、代理重启、执行进程接管、ADB/Fastboot身份切换、本地锁与 Artemis 锁域、共享 Hub 同时连接及多手机并发。平台和引擎环境保持分离。

建议按 1 台设备 → 同机 2 台 → 同机目标负载 → 3 节点扩展，记录 CPU/内存/USB 带宽/磁盘/模型吞吐和故障结果。约 60 台设备是后续验收目标，M0 未设置或承诺任意固定并发上限。
