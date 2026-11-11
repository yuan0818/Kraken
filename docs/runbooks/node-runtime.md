# 节点运行与无 SQLite 升级

2026-11-11 起，节点运行时不再读写 SQLite。控制面 PostgreSQL 统一保存命令、启动许可、执行状态、租约与已接收事件。Artemis 内核本身的会话存储不在本次改动范围。

## 配置与启动

用 `NODE_STATE_DIR` 替代 `NODE_JOURNAL`。保留 `NODE_ID`、`API_URL`、引擎及设备配置。每个逻辑节点只能启动一个 runtime；真机节点仍以一个 SN 为单位，每个 SN 使用不同 NODE_ID 和状态目录。

```sh
# 模拟节点（同时包含通信与监督，不再另起 supervisor）
python -m node_agent.transport.client

# 指定 SN 真机节点，Artemis 引擎先单独启动
python -m node_agent.supervisor.artemis --serial <SN>
```

`NODE_STATE_DIR` 包含：

- `identity.json`、`runtime.lock`：身份绑定与进程互斥。
- `events/*.json`：尚未获控制面确认的观察事实，包含稳定事件 UUID。
- `reports/*.json`：待上传报告，成功确认后移除。
- `attempts/<id>/`：命令快照、启动回执、会话、检查与原始证据。

没有本地作业表、租约表或调度游标。JSON 文件用 fsync 和原子替换保存；证据文件是本地恢复缓冲，需要保留持久目录。事件 ACK 丢失时重传相同 UUID，控制面在业务事务中去重。报告上传和终态上报独立重试，页面允许结果先到、截图稍后补齐。

## 从旧节点升级

1. 暂停向待升级节点分配新工作，检查活动执行及远控；优先等当前用例结束。停止旧通信进程与监督器，保留本机 Artemis 和原状态目录。
2. 备份控制面数据库后执行 `alembic upgrade head`（0014）。旧的已接受或活动 execute 命令会保守标记为已经发出启动许可，升级后不能再次启动。
3. 在每个节点运行一次导入工具，source 指向旧 SQLite，state-dir 指向新的持久目录（也可为原目录）。真机加 `--serial`，模拟节点不要加。

```sh
python scripts/migrate_node_journal.py \
  --source /var/lib/kraken/journal.sqlite3 \
  --state-dir /var/lib/kraken \
  --node-id linux-01
```

导入工具只读旧库，核对节点身份，迁移未确认事件和待上传报告，复制尝试证据并写入迁移标记。不会删除旧 SQLite。重复执行不会重新导入已经确认的事件。若原有事件与中央历史不一致，会拒绝同步并暂停接收新工作，需要人工核对原备份，不能清空缓冲来绕过错误。

4. 删除环境变量 `NODE_JOURNAL`，设置 `NODE_STATE_DIR`。systemd 停用旧 `artemis-platform-simulator.service`，只启用新版 `kraken-node.service`；真机服务 ExecStart 使用上面的真机入口。Pod 模拟模板每节点也只启动一个 agent 容器。
5. 启动节点，检查 `nodes.runtime_protocol=2`、心跳与设备状态。节点首先补传旧事件，再读取中央活动工作；有启动许可的尝试只查询原会话。旧库先作为升级备份保留，不再被运行时打开。

## 故障语义

| 场景 | 行为 |
| --- | --- |
| 控制面不可用 | 不接受新启动；已经运行的 Artemis 继续，节点收集结果到文件，恢复网络后补报 |
| runtime 进程重启 | 新 boot_id 获取代次，从控制面恢复活动租约与命令，再查询 Artemis 会话；不重复启动 |
| 启动许可请求超时 | 同一执行命令重试只返回已存在许可；无法证明已启动时等待原会话，120 秒后待恢复并保留设备占用 |
| 事件已提交但 ACK 丢失 | 相同 UUID、相同内容重复确认，不产生第二次业务更新 |
| 相同 UUID 不同内容或租约不符 | 拒绝并保留本地文件，停止新启动，供人工对账 |
| 本地证据磁盘丢失 | 已确认的中央业务状态不受影响；未上传证据可能丢失。启动许可仍能防止因节点本地记录消失而重跑 |
| 控制面恢复旧备份 | 暂停调度，核对节点、SN、中央活动租约、原生会话及待传事件；不能认为备份回退可自动无损恢复 |

新 boot_id 会使旧代次的启动和事件请求失效；boot_id/generation 用于并发控制，不是认证 Token。当前为用户选定的内网免节点认证模式。

## 验证范围

`tests/integration/test_node_runtime.py` 验证事务启动许可、并发重试、旧进程代次失效、ACK 丢失、非法事件与旧游标迁移。`test_node_process.py` 启动独立 HTTP API 和模拟节点，杀死 API、等待离线完成、再杀死并重启节点与 API，验证补报、单次启动许可及租约释放。测试使用隔离 PostgreSQL schema，不接手机。

`tests/contracts/test_physical_recovery.py` 使用替身引擎验证提交响应丢失不会重复 submit，以及无法确认会话时不声称清理完成。此项不等于真机故障注入验收。Linux 主机断电、Hub 掉线、刷机中断、原生引擎任意步骤续跑仍需专项验收。
