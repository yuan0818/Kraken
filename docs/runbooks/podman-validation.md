# Podman Linux 多节点验证记录

日期：2026-10-29（Asia/Taipei）。环境：本机 Podman Linux ARM64，独立 `artemis-lab` 网络、一个控制面 Pod、三个节点 Pod；每节点两个非 root 容器，分别运行代理与模拟监督器。API 使用受实验室 CA 信任的 HTTPS/WSS，地址为 `https://127.0.0.1:18100`。

## 实际结果

| 验证 | 结果与依据 |
| --- | --- |
| 节点与清单 | 三节点各注册 20 个唯一模拟 SN，共 60 个设备；错误 token 返回 403 |
| 同时并发 | 60 个尝试曾同时为 running，三个节点各持有 20 个活动设备租约 |
| 本机互斥 | 三个节点的第二个监督器锁竞争均由 Linux 文件锁拒绝 |
| 代理重启 | 第一节点代理重启后，其独立监督器 StartedAt 未改变 |
| 监督器崩溃 | 第二节点监督器 SIGKILL 后重启，持久模拟作业恢复 |
| 节点重建 | 第三节点 Pod 删除并重建，Pod ID 改变；日志卷、身份及历史执行保持 |
| 控制面重启 | API 重启后 WSS 恢复，60 个并发尝试均完成；每个作业 starts=1，Stable 快照未改变 |
| 网络隔离 | 实际移除第一节点 Pod 的网络连接；监督器离线完成并积压事件，控制面收到清理结果前仍持有租约；过期节点的新任务保持 queued，其他节点继续运行 |
| 补传 | 恢复网络后离线完成事件补传、确认，原租约释放，不重新启动已完成尝试 |
| 取消与重试 | 排队取消及运行中取消完成；配置一次重试生成编号 1、2 的两个独立尝试；所有活动租约最终为 0 |
| 整栈停止再启动 | `down` 保存日志、移除本实验室 Pod 后，`up --skip-build` 重建；60 个用例、6 个批次、66 个尝试与三个节点全部作业/游标保持一致，活动租约仍为 0 |
| 原开发环境 | 原 `8100/healthz` 与 `5178/` 仍返回 200；未重启或修改原开发数据库与其他项目容器 |

模拟完成结果均为 `completed / inconclusive / verified / partial`。上述并发仅验证调度与状态管理，没有启动 60 个真实 Artemis 实例，也没有得到任何真机 PASS。

## 自动检查

- 原有 70 项加本轮节点配置、清单校验、日志身份和执行清单检查 18 项，共 **88 项**，在 macOS 与 Linux ARM64 容器各通过一次。
- 新增实验室名称及资源归属保护 **5 项**通过，验证同名外部 Pod/容器在移除或重启之前被拒绝。因此本轮自动测试共 **93 项通过，其中 88 项完成 Linux 容器复验**。
- Ruff 检查及 44 个 Python 文件格式检查通过。保留既有 Starlette TestClient/httpx 弃用警告。本轮没有前端变更，未重复浏览器验收。

## 证据与版本

自动故障验证运行于 12:15:25～12:18:47（Asia/Taipei）：

- `.runtime/pod-labs/artemis-lab/verification-LAB-fbb9c3b8.json`：8 组检查通过、每节点峰值租约数与 6 个执行批次 ID。
- `.runtime/pod-labs/artemis-lab/recreation-check.json`：整栈移除/重建前后对比；三个节点事件序号分别为 115、105、106。
- `.runtime/pod-labs/artemis-lab/logs/`：整栈停止前保存的 API、消息队列、调度器和各节点日志。断网导致的连接中断/重试日志是主动故障注入结果。
- `control-build.log`、`node-build.log`、`linux-test-build.log` 位于同一实验室目录。凭据文件不进入版本库或报告。

镜像 ID：

```text
control: 64aab3fcc68c1a4536df76274b9c21849db2c3cf937d6533b6e1a41dd0c7154a
node:    fb524a495eadd5c415d1e8e9bc0d25e1baac5c5f72b2fe0c4815ab769af84286
```

本轮完成 FND-04 和 NOD/SCH 相关模拟验证子范围。真实节点工具链、Artemis/模型实际调用、systemd 与整机恢复、USB/Hub、Fastboot、刷机、远控、NAS 及真实设备容量仍需各自接入和验收。操作入口见 [本地 Podman 实验室](podman-lab.md)。
