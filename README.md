# Kraken

**English** | [简体中文](README.zh-CN.md)

Kraken is a standalone, all-in-one UI testing platform. A single logical control plane manages multiple execution nodes, while the execution engine runs as a separately deployed external service.

The platform supports simulated scheduling and physical-device execution targeting a specific serial number (SN), task-level model selection, remote device control, step screenshots, and ANR/Crash collection. See the [architecture overview](docs/architecture/overview.md) for component responsibilities and implementation boundaries.

An isolated Podman lab is available for local Linux multi-node validation. Run `uv run python scripts/pod_lab.py up` to start one control-plane pod and three node pods, with 60 simulated devices by default. Use `verify` to check concurrency and failure handling, and `down` to stop the lab while retaining its data. The lab does not modify the existing development stack. See the [Podman lab guide](docs/runbooks/podman-lab.md).

## Development setup

Requirements: Python 3.12, uv 0.13.4, Node.js 22+, pnpm 11.25.0, and either Docker Compose or Podman with podman-compose. Python and frontend dependency versions are locked in `uv.lock` and `pnpm-lock.yaml`, respectively.

```sh
uv sync --frozen --all-packages
pnpm install --frozen-lockfile
uv run python scripts/init_dev_env.py
docker compose --env-file .env -f deploy/compose/dev.yaml up -d
uv run alembic upgrade head
# Create once; existing keys are not overwritten. Restore this key alongside database backups.
uv run python scripts/init_secret_key.py .runtime/secrets/master.key
uv run python scripts/dev.py
```

Environment initialization generates random database and message-broker passwords and writes them to `.env` with file permissions of `0600`. For an existing environment, retain its configuration and volumes, and set `POSTGRES_PASSWORD` and `RABBITMQ_PASSWORD` locally to match the connection URLs. Wait for PostgreSQL and RabbitMQ to become healthy before running migrations. For Podman, use `podman-compose --env-file .env -f deploy/compose/dev.yaml up -d`. Deployment tools must be installed separately; local tool directories are not part of the release.

- Web UI: <http://127.0.0.1:5178>
- API documentation: <http://127.0.0.1:8100/docs>
- Health: `/healthz`; dependency readiness: `/readyz`.
- Local ports: PostgreSQL `56432`, RabbitMQ AMQP `56732`, RabbitMQ management UI `56733`.
- `Ctrl-C` stops the application processes started by the development script. Containers and data volumes remain intact. Application logs are stored in `.runtime/logs/`.

### Test tasks

Create and maintain cases, mark a version as Stable, then create a test task containing a set of cases. Add enabled cases through the filterable selection dialog, configure parallel or sequential execution, scheduling, and retries, and save the task. Run it immediately or wait for a scheduled trigger, then inspect each execution batch and all attempts for each case.

Creating a task does not start a test. Enabled cases without a Stable version can be added to a task, but a usable Stable version is required before execution. Each attempt reads the current Stable version when its start is authorized. See the [test task guide](docs/runbooks/test-tasks.md).

### Case management

Maintain modules, versions, and device models in **Reference Data (基础数据)** before creating Android OS or App cases. OS cases use Android versions and chipset platforms; App cases use the application's own versions, with modules and versions belonging to a specific App. Case identifiers remain fixed. Content, status, and the reference-data names at the time of editing are stored with each version.

Steps and expected results support Markdown and are converted to plain text for execution. The case library supports combined server-side filters. Existing schema 1 content remains viewable; structured fields must be completed when editing it. Physical-device inventory collects ADB state, device model, and product identifiers. Automatic mapping to structured applicability criteria and APK management are not yet integrated.

### Case imports

Open **Case Management → Excel Import**, or **Import Center (导入中心)** in the sidebar. Upload a file, map columns, preview the results, and accept the changes. Unchanged content does not create a new version. Conflicts require row-by-row confirmation, and accepting imported content never moves the Stable pointer.

The source name, worksheet name, and case identifier together form a stable source identity. Keep the source and worksheet names unchanged for subsequent imports. Original files and all preview/acceptance results remain traceable. See the [case import guide](docs/runbooks/case-imports.md) for formats, limits, and concurrency rules.

### Model configuration

Open **Model Configuration (模型配置)** in the sidebar. Configure the ID (the `model` value sent in requests), name, API schema, URL, key, provider, context window size, and reasoning effort. Keys are write-only and are never displayed back to the user. Leaving the key blank when editing retains it; an explicit clear action removes it.

Select a configuration under the task's execution model setting. When a batch is triggered, its model configuration and credential version are frozen. Subsequent edits, key rotation, or disabling the configuration do not change existing batches. A control-plane text check does not replace node, vision, or physical-device validation. See the [model configuration guide](docs/runbooks/model-configurations.md) for key deployment, protocol mapping, and limitations.

The development environment uses loopback HTTP/WS. Deployments may use HTTPS/WSS. Nodes do not require tokens; database and message-broker passwords are generated during deployment setup.

## Validation

```sh
uv run ruff check apps packages tests migrations scripts
uv run ruff format --check apps packages tests migrations scripts
uv run pytest -q
pnpm --filter @kraken/web test
pnpm build
# Stop scripts/dev.py first to free port 8100; keep PostgreSQL and RabbitMQ running.
uv run python scripts/stack_smoke.py
```

Integration tests apply real PostgreSQL migrations in dedicated test schemas and remove only the randomly named `test_*` schemas they create. Set `TEST_DATABASE_URL` to use a dedicated test database; do not point it at production. Process-failure tests stop only their own child processes and retain simulated execution records.

Generate API contracts and client types:

```sh
uv run python scripts/export_contracts.py
pnpm exec openapi-typescript contracts/generated/openapi.json -o packages/web-api-client/src/schema.d.ts
```

Nodes connect to the control plane over internal HTTP/HTTPS and WS/WSS without node tokens. Node IDs, device leases, and command ownership are still validated during execution. User access is open by default. Deployers can extend `packages/identity` to integrate LDAP or other authentication systems.

## Repository structure

| Path | Responsibility |
| --- | --- |
| `apps/control-plane` | FastAPI, business scheduler, outbox publisher, Celery workers, and transactional rules |
| `apps/node-agent` | HTTP status reporting, pending event/evidence files, local locks, simulated and SN-targeted physical-device execution supervision, diagnostics, remote control, and evidence uploads |
| `apps/web` | React/TypeScript/Vite/Ant Design frontend with separate overview, case, task, device, and node pages |
| `packages/contracts` | Shared DTOs, state enums, and JSON Schema definitions |
| `packages/identity` | Access principals and permissions; currently open access, with LDAP and other authentication left to deployer extensions |
| `packages/web-api-client` | Generated API types and request wrappers |
| `migrations` | Alembic and versioned PostgreSQL DDL |
| `deploy` | Development Compose configuration, control-plane deployment templates, and standalone systemd units |
| `tests` | Protocol, database consistency, recovery, and hardware acceptance checks and boundaries |

Further documentation (currently in Chinese): [architecture overview](docs/architecture/overview.md), [domain model and protocols](docs/architecture/domain-protocol.md), [node runtime](docs/runbooks/node-runtime.md), [remote devices](docs/runbooks/remote-devices.md), and [quality inspection tools](docs/runbooks/quality-tools.md).

The external execution engine is not bundled with this repository. This release does not establish acceptance of 60-device physical hardware capacity, resuming from arbitrary test steps, or control-plane host-level high availability.

## Release scope

The platform is intended for deployment on a trusted internal network. User access is open by default, and nodes do not use tokens. Do not expose this open-access mode directly to the public internet. Extend `packages/identity` to add authentication. The repository contains platform source code, contracts, tests, and deployment templates; runtime data and credentials are excluded from version control.

The open-source license is pending confirmation by the project owner. No unapproved license is included in this release. The execution engine and optional inspection services are deployed separately.

Kraken uses its own Compose project, image, and node service names. When migrating an existing deployment, explicitly verify database, volume, and encryption-key locations. The new templates do not automatically migrate data from older instances.
