# 领域模型与节点运行协议

## 权威数据与边界

PostgreSQL 保存业务事实；RabbitMQ 保存待消费消息；Celery 只完成短时投递协调。命令、启动许可和执行状态统一保存在 PostgreSQL；节点仅保存待确认事件与报告文件。Web 只调用控制面。当前 Web 轮询展示状态，截图、图文步骤报告及 WSS 远控视频已接入。

Case 保存身份和 `stable_version_id`。每次修改创建 CaseVersion，数据库拒绝 UPDATE/DELETE；复合外键拒绝 Stable 指向其他 Case 的版本。TestTask 保存可编辑的用例集合与执行规则，每次保存生成不可变 TaskRevision。Run 是一次执行批次，冻结任务配置与用例 ID 集合；CaseRun 表示 Run 中某用例，Attempt 表示一次尝试。M0 包身份仅为显式传入值；SMB/FTP 最新包解析、摘要核验属于后续任务。

Attempt 的版本在节点 `ready` 事件处理事务中解析并形成不可变快照，此时即平台的**开始授权点**。事务锁定 Case，校验当前 Stable 与设备标签，并持久化 execute 命令；运行中及重连不再读取 Stable。分布式系统不能把数据库读与手机第一步做成一个事务。新 attempt 重新读取 Stable；同一 Run 的包引用保持不变。

Schedule、ImportBatch、Evidence、Subject、Audit 已有初始表。初始表本身不代表对应产品能力完成。Excel 导入由迁移 0004 接入；任务每日/每周定时、同任务批次排队、只补最近一次及有限重试由迁移 0005 接入。当前任务定时配置保存在 TestTask，初始 Schedule 表未作为第二套调度事实使用；完整任务链、包解析、留存与 SSO 仍待接入。

## 结构化用例与基础数据

迁移 0003 为 Case 添加唯一编号，并新增 `catalog_entries`、`case_version_catalog_refs`。旧用例自动补编号，历史版本 JSON 不重写。CaseContent schema 2 要求模块与测试目的，App 还要求所属 App；schema 1 兼容已有内容。编号、名称属于 Case，目的、前置条件、步骤、预期结果、级别、状态及适用范围随版本保存。

基础数据分为系统模块、App、App 模块、App 版本、Android 版本、机型、芯片平台。App 模块和版本有不可变的 App 归属，后端校验同一 App 关系；App 内容拒绝 Android 版本和芯片平台，OS 内容拒绝 App 及 App 版本。保存版本时校验所有引用，并由服务端生成 ID 到名称的 `catalog_snapshot`，客户端不能伪造历史名称。引用过的条目不能删除；停用影响新版本引用，不回写已有版本。

- `GET /api/cases`：编号、目的、对象、模块、App、级别、状态、Android/App 版本的组合检索，以及通用关键词、服务端分页；以最新编写版本为检索对象，Stable 单独返回。
- `GET/POST /api/catalogs`、`PUT/DELETE /api/catalogs/{id}`：基础数据管理，写入审计；重复名称、错误归属、引用中删除返回可读冲突。
- 执行准入、分配设备及开始授权点检查 Stable 状态；仅 `active` 可开始。新版本保存不修改 Stable。
- 适用范围同维度 OR、跨维度 AND，附加标签要求全部满足。M0 使用节点上报的 `android_version:<UUID>`、`app_version:<UUID>`、`device_model:<UUID>`、`chip_platform:<UUID>` 标签匹配；未上报对应值的设备不能匹配受限用例。真实设备属性采集和 App 安装后版本核对属于节点接入阶段，基础数据条目本身不是实测设备事实。
- 调度器跳过暂时没有匹配设备的任务，继续寻找后续可执行任务；开始授权时重新核对最新 Stable 的范围，避免排队期间变更后误用已预占设备。

## 状态与租约

| 状态 | 进入条件 | 设备占用 |
| --- | --- | --- |
| queued | Run 提交或显式重跑 | 尚未占用 |
| preparing | 调度器事务分配设备并创建 prepare/Outbox | 保留 |
| starting | 节点 ready，Stable 校验通过，execute 已持久化 | 保留 |
| running | 节点回传 started | 保留 |
| cancel_pending | 持久取消意图，撤回未交接命令，等待节点停止 | 保留 |
| completed | 收到完成且清理验证通过 | 释放 |
| cancelled | 排队时取消，或节点确认取消和清理 | 释放 |
| blocked | 命令拒绝且已证明无需清理或已清理 | 释放 |
| needs_recovery | 清理失败、取消失败或无法证明安全结束 | 保留；禁止重跑 |

执行状态、verdict、cleanup、evidence 四个字段独立。当前 simulator 的正常完成是 `completed / inconclusive / verified / partial`，数据库拒绝模拟 PASS。Celery SUCCESS 不参与断言结论。

设备身份为资产 UUID + node_id + ADB endpoint + SN，SN 是寻址条件。控制面分配递增 generation，活动租约有设备和 attempt 两个部分唯一索引。本机监督进程持有原子文件锁；断线/心跳超时不释放租约。远控使用独立输入租约，禁止干预自动执行。真实 Artemis 锁交接必须由 ENG-01 统一，不能由平台与子进程重复获取非重入锁。

单活调度使用事务级 advisory lock；断开 DB 即失去所有权并停止新的提交。API/调度/事件更新均为短事务。未知执行不会仅因租约时间或节点离线而自动转为空闲。

## 当前 HTTP 运行协议

迁移 `0014` 引入 `nodes.runtime_protocol`、`commands.launch_intent_at`、`node_runtime_sessions` 和 `node_runtime_events`。

- 模拟节点通过 `POST /api/nodes/register` 注册；真机使用 ADB inventory 接口。节点无需 Token。
- `POST /api/nodes/{node_id}/runtime` 提交每次进程启动生成的 `boot_id`，返回 generation。相同 boot_id 重试不会增加代次；新的 boot_id 使旧代次失效。generation 是并发隔离标识，不是认证凭据。
- 后续请求带 `X-Node-Generation`。`GET /runtime` 返回中央活动租约、执行状态、原始执行命令、当前可处理命令及启动许可时间。
- `POST /commands/{command_id}/claim` 在事务内检查节点、设备 SN、租约代次、投递状态、尝试状态和过期时间；首次 execute 保存启动许可并返回 `start=true`。已有许可只返回 `start=false`，节点查询原 Artemis 会话，不能重新提交。
- `POST /events` 接收 `NodeUpdate`。事件包含 UUID、命令、尝试、租约代次及观察事实；节点先落文件，再发送。控制面验证事件，在业务更新同一事务中记录 UUID 去重，确认后节点删除文件。重复 UUID 和相同内容可重复确认，不同内容拒绝。
- 节点不生成新的全局递增游标。控制面内部仍分配 `node_events.seq` 以保留历史；`legacy_seq` 仅用于一次性旧日志迁移和 ACK 丢失校验。
- 旧 WS/WSS v1 接口保留用于未升级节点；一旦节点使用新 runtime 接口，拒绝它再走旧命令通道。远控视频 WSS 不受影响。

同一节点仅启动一个 runtime 进程，进程持有 `runtime.lock`。模拟多设备在进程内并发，真机入口仍绑定一个显式 SN。命令及启动许可恢复自控制面，本地文件不作为调度事实来源。配置和旧文件迁移见[运行说明](../runbooks/node-runtime.md)。

取消可以在 prepare 前到达，节点保留取消事实，迟到 prepare 不会启动。任务已完成后到达的取消确认保留为历史事件，不覆盖首个终态，也不堵住事件游标。过期、租约代次不符、状态未知等走拒绝/恢复，不退回首台在线设备。

OpenAPI、JSON Schema 位于 `contracts/generated/`；类型源为 `packages/contracts` 与 API 响应模型。命令内容 schema 仍为 v1，运行连接协议由 runtime_protocol 区分；未知字段拒绝。增加新命令/事件先更新契约与兼容测试，再协调升级节点。

## 可靠交接与故障边界

业务变更与 Outbox 同事务。发布器先声明持久 exchange/queue/binding，再以持久消息和发布确认发送；数据库记录确认之前退出，可重发同一命令。若确认发布后 30 秒仍无持久消费交接，发布器重投原 command_id；这是投递恢复，不创建新 attempt。消费者幂等标记 dispatch_ready，节点 HTTP 查询持久命令；启动许可与完成事实独立。节点通信与监督合为一个 runtime，Artemis 引擎独立运行；runtime 重启后查询中央活动租约及引擎会话。

历史 v1 验证包含 DB 事务约束、重复命令、WSS 重连与 broker 交接。当前 runtime 回归包含并发启动申请、ACK 丢失、事件冲突、旧启动代次失效、旧日志导入，以及独立 API/节点进程的离线完成与重启补报。还未测：Celery prefork 子进程恰在事务提交与 ACK 之间崩溃、RabbitMQ/数据库主机断电、实际 Artemis 进程接管与 Fastboot 中断。`acks_late` 与 `reject_on_worker_lost` 的配置不是上述场景的测试证据。

参考：[Celery 配置](https://docs.celeryq.dev/en/stable/userguide/configuration.html)、[RabbitMQ 确认机制](https://www.rabbitmq.com/docs/confirms)、[SQLAlchemy PostgreSQL](https://docs.sqlalchemy.org/en/20/dialects/postgresql.html)。
# 用例导入增补（2026-10-22）

迁移 `0004_imports` 增加 `import_uploads`、批次预览/采纳配置和 `case_source_links`。来源稳定身份、逐行校验、幂等与事务规则见 [导入说明](../runbooks/case-imports.md)。`StableSelection` 新增必填的 `expected_version_id`（允许 null），写入持锁比较原指针；版本编辑客户端使用 `expected_latest` 检查打开表单时观察到的最新版本。预览及采纳均不移动 Stable，也不改变已开始尝试的快照。

## 测试任务增补（2026-10-22）

迁移 `0005_tasks` 新增 `test_tasks`、有序 `task_cases`、不可变 `task_revisions`；Run 关联任务和配置版本，保存触发来源、触发时刻、配置快照、取消意图与准入阻塞原因。保存使用 `expected_revision` 比较交换；创建任务和触发批次分别用请求 UUID 幂等。定时触发使用任务 ID + 计划时刻唯一约束，审计归属独立 `scheduler` 系统主体。

调度器在同一事务 advisory lock 下依次推进定时、重试及设备分配。同任务上一批次未结束或仍持有设备租约时，后续批次排队；批次内按冻结配置限制并发或顺序执行。尝试终态由数据库记录 `finished_at`，重试间隔从该时刻计算。自动重试只适用于已结束且清理确认、设备已释放的失败/阻塞/证据不足尝试，不重试取消或状态未知的执行。取消意图与重试生成按批次行锁串行化。

`/api/tasks` 管理可复用任务，`/api/tasks/{id}/execute` 产生批次，`/api/executions` 展示批次及全部用例尝试，`/api/executions/{id}/cancel` 取消整批。旧 `/api/runs` 单用例提交端点保留兼容；新任务页面不再使用它。节点协议及 Stable 的开始授权时点不变。当前 `require_flash` 可保存但执行准入拒绝，定时记录阻塞批次，不会生成设备操作。

## 模型配置增补（2026-10-22）

迁移 `0006_model_configs` 新增模型配置容器、不可变版本、不可变加密凭据和检测记录。配置的数据库 UUID 与用户填写的请求 model ID 分开；八字段表单不增加内部版本/凭据引用字段。密文使用 Fernet，主密钥由 `PLATFORM_SECRET_KEY_FILE` 指向独立权限文件，数据库不保存主密钥。读取接口只返回 `has_key`，版本、审计、任务、批次、Outbox/命令中不含明文 Key。模型输入校验错误剔除原输入，外部错误正文不落库或回显。

模型保存使用 `expected_revision` 比较交换；Key 省略/null 保留原引用，空字符串明确清除，新 Key 生成新凭据引用。URL/schema 改变且原 Key 存在时，必须明确提供或清除 Key，防止保存操作把原凭据改投其他服务。启停同样递增版本，历史保留。

任务只存 `configuration.model_config_id`；每次 Run 触发解析模型当前版本，快照保存非敏感字段，Run 的 `(model_config_id, model_revision)` 外键固定对应版本及内部凭据引用。同批次排队和自动重试不更换模型；用例内容仍在各 attempt 开始授权点读取 Stable，这两个时点分别处理。停用阻止新批次准入，不改写已存在批次。未选择模型的旧模拟任务继续兼容。

控制面显式检测发送固定文本请求，网络调用不持有数据库事务；检测结果绑定调用前读取的配置版本，并记录 `location=control_plane`。并发编辑后旧检测不会显示为新配置验证成功。HTTP 200 还需符合协议的非空文本，重定向、错误/无效响应、超时均不通过；不保存外部正文，不删除思考参数自动重试。此通道不是设备模型执行协议；节点 WSS、真实 Artemis 和视觉验证尚未接入。
