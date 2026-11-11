# M0 实现与验证记录

日期：2026-10-21。状态：本地基础骨架与模拟闭环已落地；真实 Linux 主机、执行内核和刷机接入仍待验证。本文逐项区分实现与证据，不将技术选型确认当作产品验收。

| 任务 | 本次交付 | 验证范围 / 剩余项 |
| --- | --- | --- |
| FND-01 | 独立 Git、四个 Python workspace 包、React Web、生成契约客户端、锁文件、规划文档迁入 | Mac 可启动 API/调度器/发布器/worker/模拟节点/Web；远端未配置 |
| FND-02 | 16 张初始业务/协议表、两版 Alembic 迁移、Stable/版本/快照/租约约束、独立结果维度 | 真实 PostgreSQL 测试通过；Schedule/Import/Evidence 等仅有基础表，不代表对应产品完成 |
| FND-03 | v1 JSON Schema、注册/WSS、命令/事件幂等、连接代次、持久游标、取消与模拟对账 | 数据库与真实 WebSocket 测试通过；独立文件/媒体通道尚无服务实现 |
| FND-04 | 开发启动、Compose、Dockerfile、systemd 模板、CI、故障脚本与 Linux 检查入口 | Mac 与 Linux ARM64 容器验证通过；真实 Linux systemd、TLS 信任链、USB/Hub/60 设备容量未验收；远程 CI 尚未运行 |
| FLA-01 | 依据 PhoneAutomationTest 静态定位 GUI/脚本、参数与合并流程，形成改造清单 | 尚无可验证的部署 headless CLI；真实取包、刷写、取消和刷后验证未执行 |

## 已执行的验证

| 检查 | 结果 | 能证明什么 |
| --- | --- | --- |
| uv frozen workspace 安装 | 通过，Python 3.12.15 | 平台依赖独立；无完整 Artemis 引擎依赖 |
| PostgreSQL 18.6 迁移到 0002 | 通过 | 空 schema 可建表/约束；不是已有生产库升级验收 |
| pytest（Mac） | 20 passed | 版本不可变、跨 Case Stable 拒绝、开始授权快照、约束重校验、并发唯一租约、幂等提交、乱序/旧连接/重复事件、Outbox 确认丢失、清理失败占用、重跑 Stable、本地 WS 重连、节点认证、取消竞态等 |
| pytest（Linux ARM64 容器，连接实际 PostgreSQL） | 20 passed | Linux Python 依赖与相同协议/事务行为；不证明原生 systemd 或 USB |
| RabbitMQ 4.2.1 + Celery 真实进程闭环 | 通过 | broker/worker 交接、worker 后启动、API/节点通信 SIGKILL 后重连、监督进程存活、同 attempt 仅启动一次、事件回传、取消后释放 |
| 前端 TypeScript/Vite 构建 | 通过 | 类型与打包可用；浏览器操作另验 |
| 浏览器实操 | 通过 | 新建用例/版本、显式设置 Stable、提交模拟任务、查看已结束/不确定结论及执行快照；页面控制台已无错误 |
| Ruff | 通过 | 基础静态检查与格式 |

进程测试脚本：`scripts/stack_smoke.py`；本机原始结果：`.runtime/stack-smoke.json`；日志 `.runtime/logs/`。该脚本只停止自己创建的子进程；模拟命令没有 ADB/Fastboot 调用。首次 RabbitMQ 启动因新 Podman 卷 cookie 属主不符失败，已加入专用卷权限初始化并复验成功，未改动其他项目容器。

已知非阻断信息：当前 FastAPI/Starlette 的 TestClient 发出 httpx 迁移弃用警告；前端初始 Ant Design 主 chunk 约 1.1 MB（gzip 约 345 KB），后续 FND-05 按页面拆分。不是实际失败断言。

## 尚不能据此声称完成的能力

真实 Artemis 单设备执行与日志/截图证据、Fastboot 全量刷机、SMB/FTP/NAS 取包归档、定时/最新包/自动重跑规则、远控、飞书同步、模型配置、Google Workspace、录像与报告留存均按任务规划继续。当前仅提供模拟器及共用契约。

prefork worker 子进程在提交与 ACK 间崩溃、MQ/数据库断电、平台主机失效、系统时间跳变、持久日志损坏、Artemis 进程身份接管和刷写中断均未真实故障注入。不可把 late ACK 配置、容器启动成功或模拟恢复描述为这些场景已经验证。

M0 后续收口条件：核验真实 Linux 上的部署工具链与独立服务生命周期，按 FLA-01 记录落实无界面接入入口，确认实际版本/参数/样例包。没有执行这些验证前，M0 总体验收保留未完成状态。
## 后续用例能力增补（2026-10-22）

已加入结构化 Case 编辑/基础数据/检索、Excel 上传与预览采纳、来源幂等与冲突、版本差异及 Stable/编辑并发保护。数据库已在备份后升级至 `0004_imports`，原有记录保留；本地 40 项后端测试及前端构建通过，浏览器逐项证据见 [E2E 记录](../../tests/e2e/README.md)。此处更新用例管理能力边界，不改变上述真机及 M0 总体验收的待完成状态。
