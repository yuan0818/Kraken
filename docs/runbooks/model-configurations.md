# 模型配置与凭据

## 使用

“模型配置”提供八字段表单：ID、名称、API schema（OpenAI/Anthropic）、URL、Key、供应商、上下文窗口大小、思考强度。ID 是传给服务的 `model` 标识，可包含内网部署命名空间；平台配置另有自动生成的 UUID，用于关联任务与历史，无需用户填写。供应商是展示标签，协议由 API schema 决定。上下文窗口单位为 tokens，是用户声明值，当前连接检测不会验证或测量它。

URL 填 API 基础地址；OpenAI 示例 `https://service.example/v1`，请求追加 `/chat/completions`。Anthropic 基础地址含 `/v1` 时追加 `/messages`，不含时追加 `/v1/messages`。地址拒绝凭据、查询参数、片段及完整推理接口路径；支持内网 HTTP 和 HTTPS，不跟随重定向，不使用进程的代理环境变量。

Key 无鉴权时可留空。已保存 Key 不回显：编辑留空/省略/null 保留，填写替换，“清除已保存的 Key”明确移除。更改 URL 或 API schema 时需重新提供 Key 或明确清除。每次编辑、启停都产生配置版本；旧配置和原凭据保留供历史批次引用。

点击“检测连接”只向配置服务发送固定的 `Connectivity check. Reply with OK only.` 文本请求，输出上限为 512 tokens；不发送用例、截图或设备日志。结果注明控制面、配置版本、时间、耗时和 HTTP 状态。有效协议的非空文本才通过，错误正文不保存或回显。检测可能消耗服务额度。保存配置本身不发送模型请求。

| 思考强度 | OpenAI schema | Anthropic schema |
| --- | --- | --- |
| 服务默认 | 不发送参数 | 不发送参数 |
| 关闭 | `reasoning_effort=none` | `thinking.type=disabled` |
| 低/中/高 | `reasoning_effort=low/medium/high` | `thinking.type=adaptive` + `output_config.effort=low/medium/high` |
| 其他 | minimal、xhigh | max |

并非所有模型或兼容网关都支持这些参数；服务拒绝会明确报告失败，不会删参数或换模型重试。旧版 Anthropic 固定 `budget_tokens` 模式尚未适配。映射依据 [OpenAI Chat API](https://developers.openai.com/api/reference/resources/chat)、[Anthropic effort](https://platform.claude.com/docs/en/build-with-claude/effort) 及 [thinking](https://platform.claude.com/docs/en/build-with-claude/thinking-steering-and-cost)；以目标服务实际兼容行为为准。

任务配置选择模型后，触发批次时读取当前模型配置与凭据版本。已有批次保持原版本；用例 Stable 仍在每次 attempt 开始授权时读取。模型停用会阻止新的手动执行，定时触发记录阻塞原因；不撤销已有批次。模拟批次只记录选择。真机执行在节点凭有效执行授权领取该版本的模型与 Key，并将它们注入本次 Artemis 子进程。需要支持按任务模型的 Artemis 与节点代理同机运行。跨主机的控制面连接可使用 HTTPS；按当前内网开放模式不配置节点 Token。本机开发可用显式启用的回环 HTTP。未指定模型的旧任务仍使用节点默认配置。

## 主密钥和部署

API 使用 `PLATFORM_SECRET_KEY_FILE` 指向权限为 0600 的 Fernet 主密钥文件。初始化只执行一次：

```sh
uv run python scripts/init_secret_key.py .runtime/secrets/master.key
```

命令拒绝覆盖已有文件，不打印密钥。开发配置已有该路径，`.runtime/` 被 Git 与镜像构建忽略。主密钥缺失、权限不符或不匹配时，保存/读取凭据返回可读错误，不能降级为明文。

Linux Compose 部署须给 API 容器单独挂载主密钥，并设置容器内绝对路径。镜像运行 UID 为 10001，文件应由该 UID 可读且其他用户不可读。当前 Compose 模板通过本地环境变量 `PLATFORM_SECRET_KEY_HOST_FILE` 只读挂载，并设置容器内路径。以下展示等价覆盖方式；不要把主密钥或实际文件路径提交到业务仓库：

```yaml
services:
  api:
    environment:
      PLATFORM_SECRET_KEY_FILE: /run/kraken-secrets/master.key
    volumes:
      - /srv/kraken-secrets/master.key:/run/kraken-secrets/master.key:ro
```

主密钥与数据库分开备份，恢复时配套恢复；不要丢失或直接重生成覆盖旧主密钥。本轮实现支持单个模型 Key 的版本轮换，尚未实现主密钥批量轮换、外部 Vault/KMS 或凭据清理回收。不可变旧凭据仍可能被历史/排队批次引用，不自动删除。

## API 与验证边界

- `GET/POST /api/models`：查询/创建。读取只返回 `has_key`，不返回密文或凭据引用。
- `PUT /api/models/{id}`：带 `expected_revision` 保存新配置；过期编辑返回 409。
- `PUT /api/models/{id}/availability`：启停，带当前配置版本。
- `GET /api/models/{id}/versions`：不可变版本列表，不含 Key。
- `POST /api/models/{id}/check`：带当前配置版本，执行控制面文本检测并持久化结果。

迁移 `0006_model_configs` 前备份数据库；升级后重启 API 与调度器。已有任务无模型配置时兼容运行模拟测试。

已验证加密存储、响应/校验错误不回显、并发修改、轮换与清除、停用、模型批次冻结、两种协议实际本地 HTTP 请求、错误响应/重定向/无效文本及检测中编辑的版本归属。真实 Linux 节点访问、视觉/日志交互、上下文能力、Artemis 参数兼容与供应商模型结果尚待 ENG-01/NOD 联调。控制面检测成功不代表这些能力通过，MOD-01 不作整体完成标记。
