# Kraken

Kraken 是独立的一站式 UI 测试平台。一个逻辑控制面管理多个执行节点，Artemis 保留为外部执行内核，PhoneAutomationTest 提供刷机实现参考。

平台已接入模拟调度闭环和指定 SN 的真机执行，支持任务级模型选择、远程手机、步骤截图及 ANR／Crash 采集。已确认需求、部署方式、实现边界和验证证据见[当前架构与技术说明](docs/architecture/platform-architecture.md)。

本地 Linux 多节点验证已提供独立 Podman 实验室：`uv run python scripts/pod_lab.py up` 启动 1 个控制面 Pod + 3 个节点 Pod，默认共 60 个模拟设备；`verify` 运行并发与故障检查，`down` 停止并保留数据。它不改变现有开发栈，详见 [Podman 验证说明](docs/runbooks/podman-lab.md)。

## 开发启动

要求 Python 3.12、uv 0.13.4、Node 22+、pnpm 11.25.0，以及 Docker Compose 或 Podman + podman-compose。Python 与前端版本分别由 `uv.lock`、`pnpm-lock.yaml` 固定。

```sh
uv sync --frozen --all-packages
pnpm install --frozen-lockfile
uv run python scripts/init_dev_env.py
docker compose --env-file .env -f deploy/compose/dev.yaml up -d
uv run alembic upgrade head
# 首次创建一次；已存在时不覆盖，恢复数据库时须恢复配套密钥
uv run python scripts/init_secret_key.py .runtime/secrets/master.key
uv run python scripts/dev.py
```

本地初始化生成随机数据库密码和消息队列密码，写入权限为 `0600` 的 `.env`。已有环境须保留原配置和数据卷，并在本地填写与连接 URL 一致的 `POSTGRES_PASSWORD`、`RABBITMQ_PASSWORD`。数据库和 RabbitMQ 健康后再迁移。Podman 使用 `podman-compose --env-file .env -f deploy/compose/dev.yaml up -d`。工具需由部署者安装；本地工具目录不属于发布产物。

- Web：<http://127.0.0.1:5178>
- API 文档：<http://127.0.0.1:8100/docs>
- 健康：`/healthz`；依赖就绪：`/readyz`
- PostgreSQL：本机 56432；RabbitMQ AMQP：56732；管理页：56733。
- `Ctrl-C` 停止本脚本启动的应用进程；容器和数据卷保留。应用日志位于 `.runtime/logs/`。

页面流程：维护用例并设置 Stable → 新建测试任务（用例集合）→ 在带筛选条件的弹窗中添加已启用用例 → 配置并发/顺序、定时、刷机要求和重试 → 保存 → 立即执行或等待定时触发 → 查看执行批次及各用例全部尝试。创建任务不会执行测试；可先加入尚无 Stable 的已启用用例，执行前必须补齐可用 Stable。每次尝试仍在开始授权时读取当前 Stable。详见 [测试任务说明](docs/runbooks/test-tasks.md)。

先在“基础数据”维护模块、版本及机型，再创建 Android OS 或 App 用例。OS 使用 Android 版本和芯片平台，App 使用自身版本，模块和版本归属到指定 App。编号固定，内容、状态和当时的基础数据名称随版本保存。步骤和预期结果支持 Markdown，执行时使用纯文本。用例库支持服务端组合检索，已有 schema 1 内容仍可查看，编辑时补齐结构化字段。真机清单已采集 ADB 状态、型号和产品标识，自动映射结构化适用范围及 APK 管理待接入。

Excel 导入入口：用例管理 → Excel 导入（或侧栏“导入中心”）。上传、映射、预览后选择采纳；无变化不追加版本，冲突需逐行确认，任何采纳都不移动 Stable。来源名称 + 工作表名 + 编号作为稳定来源身份；后续导入保持前两者不变。原文件和每次预览/采纳结果均可追溯。格式、限制与并发规则见 [用例导入说明](docs/runbooks/case-imports.md)。

模型入口：侧栏“模型配置”。配置 ID（请求中的 model）、名称、API schema、URL、Key、供应商、上下文窗口大小和思考强度。Key 只写不回显；编辑留空保留，明确清除才移除。任务的“执行模型”选择配置，触发时冻结当前模型与凭据版本；后续编辑、轮换或停用不修改已有批次。控制面文本检测不替代节点、视觉或真机验证。密钥部署、协议映射和限制见 [模型配置说明](docs/runbooks/model-configurations.md)。

开发环境使用 loopback HTTP/WS。部署可使用 HTTPS/WSS；节点无需 Token，数据库与消息代理密码在部署时生成。

## 验证

```sh
uv run ruff check apps packages tests migrations scripts
uv run ruff format --check apps packages tests migrations scripts
uv run pytest -q
pnpm build
# 先停掉 scripts/dev.py，释放 8100；保留 PostgreSQL/RabbitMQ
uv run python scripts/stack_smoke.py
```

集成测试在专用测试 schema 中应用真实 PostgreSQL 迁移，仅删除本次创建的随机 `test_*` schema。可用 `TEST_DATABASE_URL` 指向专用测试数据库；不要指向生产服务。进程故障测试仅停止自己启动的子进程，并保留模拟运行记录。

契约生成：

```sh
uv run python scripts/export_contracts.py
pnpm exec openapi-typescript contracts/generated/openapi.json -o packages/web-api-client/src/schema.d.ts
```

节点通过内网 HTTP／HTTPS 与 WSS／WS 连接控制面，无需配置节点 Token。节点 ID、设备租约和命令归属继续参与执行校验。用户访问默认开放，LDAP 等认证由部署方通过 `packages/identity` 扩展。

## 工程边界

| 路径 | 职责 |
| --- | --- |
| `apps/control-plane` | FastAPI、业务调度器、Outbox 发布器、Celery worker、事务规则 |
| `apps/node-agent` | HTTP 状态上报、待传事件/证据文件、本机锁、模拟与指定 SN 真机监督、诊断、远控及证据上传 |
| `apps/web` | React/TypeScript/Vite/Ant Design 平台前端，按总览、用例、任务、设备、节点拆分页面 |
| `packages/contracts` | 公共 DTO、状态枚举和 JSON Schema 来源 |
| `packages/identity` | 访问主体与权限；当前为开放模式，LDAP 等认证由部署方扩展 |
| `packages/web-api-client` | 生成的 API 类型及请求封装 |
| `migrations` | Alembic + 版本化 PostgreSQL DDL |
| `deploy` | 开发 Compose、控制面部署模板、独立 systemd 单元 |
| `tests` | 协议、数据库一致性、恢复及硬件验收边界 |

详见[需求](docs/requirements/platform.md)、[任务规划](docs/requirements/task-plan.md)、[技术方案](docs/architecture/m0-proposal.md)、[已确认决策](docs/decisions/m0.md)、[领域与协议](docs/architecture/domain-protocol.md)、[PhoneAutomationTest 接入核验](docs/architecture/flash-intake.md)、[验收记录](docs/runbooks/m0-validation.md)。

没有复制、改写或打包 Artemis/PhoneAutomationTest 执行内核。本仓库不提供 60 台真机容量、任意步骤续跑或控制面整机高可用的验收结论。

## 发布范围

平台以可信内网部署为前提，默认免登录且节点无 Token；不要把开放模式直接暴露到公网。身份认证通过 `packages/identity` 扩展。仓库仅包含平台源码、契约、测试和部署模板，运行数据与凭据不纳入版本控制。

开源许可证尚待项目所有者确认，本次发布不添加未经确认的许可证。Artemis 与 PhoneAutomationTest 作为外部执行依赖单独部署。

已有旧平台部署应先保留原 Compose 项目名和数据卷，再按迁移说明升级；Kraken 模板使用新的默认资源名称，不能直接视为原数据卷的重命名。
