"""Isolated Linux simulator lab. Never attaches USB or changes the native dev stack."""

import argparse
import ipaddress
import json
import os
import re
import secrets
import ssl
import subprocess
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
from cryptography import x509
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

ROOT = Path(__file__).resolve().parents[1]
LABEL = "io.artemis.pod-lab"
CONTROL_IMAGE = "localhost/kraken:pod-lab"
NODE_IMAGE = "localhost/kraken-node:pod-lab"
PYTHON_IMAGE = "docker.io/library/python:3.12.15-slim"
POSTGRES_IMAGE = "docker.io/library/postgres:18.6"
RABBIT_IMAGE = "docker.io/library/rabbitmq:4.2.1-management"


def podman(*args, check=True, timeout=180):
    result = subprocess.run(
        ["podman", *map(str, args)], capture_output=True, text=True, timeout=timeout, cwd=ROOT
    )
    if check and result.returncode:
        raise RuntimeError(f"podman {' '.join(map(str, args[:2]))}: {result.stderr[-1800:]}")
    return result


def wait(check, description, timeout=90):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = check()
            if result:
                return result
        except (httpx.TransportError, KeyError, IndexError):
            pass
        time.sleep(0.5)
    raise RuntimeError(f"Timed out: {description}; inspect this lab's container logs")


def private_file(path, data):
    with path.open("xb") as file:
        os.fchmod(file.fileno(), 0o600)
        file.write(data if isinstance(data, bytes) else data.encode())


def certificates(directory, hostname):
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Artemis local lab CA")])
    ca = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
        .issuer_name(ca_name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=90))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName(
                [
                    x509.DNSName(hostname),
                    x509.DNSName("localhost"),
                    x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                ]
            ),
            critical=False,
        )
        .add_extension(x509.ExtendedKeyUsage([x509.oid.ExtendedKeyUsageOID.SERVER_AUTH]), False)
        .sign(ca_key, hashes.SHA256())
    )
    private_file(directory / "ca.pem", ca.public_bytes(serialization.Encoding.PEM))
    private_file(directory / "server.pem", cert.public_bytes(serialization.Encoding.PEM))
    private_file(
        directory / "server.key",
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )
    private_file(directory / "master.key", Fernet.generate_key() + b"\n")
    # The disposable lab CA signing key is intentionally not persisted.


class Lab:
    def __init__(self, name="artemis-lab"):
        if not re.fullmatch(r"artemis-lab(?:-[a-z0-9-]{1,32})?", name):
            raise ValueError("Name must be artemis-lab or artemis-lab-<lowercase suffix>")
        self.name = name
        self.directory = ROOT / ".runtime/pod-labs" / name
        self.manifest = self.directory / "lab.json"
        self.config = json.loads(self.manifest.read_text()) if self.manifest.exists() else None

    def initialize(self, port, devices, duration):
        if self.config:
            return
        self.directory.mkdir(parents=True, mode=0o700)
        tls = self.directory / "tls"
        tls.mkdir(mode=0o700)
        self.config = {"id": str(uuid4()), "port": port, "devices": devices, "duration": duration}
        certificates(tls, self.resource("control"))
        password, broker_password = secrets.token_hex(24), secrets.token_hex(24)
        values = {
            "DATABASE_URL": f"postgresql+psycopg://platform:{password}@127.0.0.1:5432/platform",
            "BROKER_URL": f"amqp://platform:{broker_password}@127.0.0.1:5672/platform",
            "AUTH_MODE": "open",
            "PLATFORM_SECRET_KEY_FILE": "/config/master.key",
            "SCHEDULER_POLL_SECONDS": "0.1",
        }
        self.write_env("control", values)
        self.write_env(
            "postgres",
            {"POSTGRES_USER": "platform", "POSTGRES_PASSWORD": password, "POSTGRES_DB": "platform"},
        )
        self.write_env(
            "rabbitmq",
            {
                "RABBITMQ_DEFAULT_USER": "platform",
                "RABBITMQ_DEFAULT_PASS": broker_password,
                "RABBITMQ_DEFAULT_VHOST": "platform",
            },
        )
        for i in range(1, 4):
            node_id = self.resource(f"node-{i}")
            self.write_env(
                f"node-{i}",
                {
                    "NODE_ID": node_id,
                    "API_URL": f"https://{self.resource('control')}:8100",
                    "NODE_STATE_DIR": "/state",
                    "SSL_CERT_FILE": "/state/ca.pem",
                    "SIM_DEVICE_SERIALS": json.dumps(
                        [f"SIM-N{i}-{j:03}" for j in range(1, devices + 1)]
                    ),
                    "SIM_DEVICE_TAGS": json.dumps(["simulator", "pod-lab", f"lab-node-{i}"]),
                    "SIM_DURATION": str(duration),
                },
            )
        private_file(self.manifest, json.dumps(self.config, indent=2) + "\n")

    def resource(self, suffix):
        return f"{self.name}-{suffix}"

    def write_env(self, name, values):
        private_file(
            self.directory / f"{name}.env", "".join(f"{k}={v}\n" for k, v in values.items())
        )

    def label_args(self):
        return ["--label", f"{LABEL}={self.config['id']}"]

    def inspect(self, kind, name):
        result = podman(kind, "inspect", name, check=False)
        if result.returncode:
            return None
        value = json.loads(result.stdout)[0]
        labels = (
            value.get("Labels") or value.get("labels") or value.get("Config", {}).get("Labels", {})
        )
        if not self.config or labels.get(LABEL) != self.config["id"]:
            raise RuntimeError(f"Refusing unrelated {kind}: {name}")
        return value

    def ensure_volume(self, suffix):
        name = self.resource(suffix)
        if not self.inspect("volume", name):
            podman("volume", "create", *self.label_args(), name)

    def ensure_pod(self, suffix, publish=False):
        name = self.resource(suffix)
        if not self.inspect("pod", name):
            args = [
                "pod",
                "create",
                "--name",
                name,
                "--hostname",
                name,
                "--network",
                self.resource("network"),
                "--network-alias",
                name,
                *self.label_args(),
            ]
            if publish:
                args += ["-p", f"127.0.0.1:{self.config['port']}:8100"]
            podman(*args)

    def container(self, suffix, pod, image, command=(), env=None, options=()):
        name = self.resource(suffix)
        existing = self.inspect("container", name)
        if existing:
            if not existing["State"]["Running"]:
                podman("start", name)
            return
        args = [
            "run",
            "-d",
            "--name",
            name,
            "--pod",
            self.resource(pod),
            *self.label_args(),
            "--stop-timeout",
            "5",
            "--log-opt",
            "max-size=10mb",
        ]
        if env:
            args += ["--env-file", self.directory / f"{env}.env"]
        podman(*args, *options, image, *command)

    def prepare_volumes(self):
        init = self.resource("init")
        if self.inspect("container", init):
            podman("rm", "-f", init)
        volumes = ["config", "mq", *(f"node-{i}-state" for i in range(1, 4))]
        options = [arg for v in volumes for arg in ["-v", f"{self.resource(v)}:/vol/{v}"]]
        podman(
            "run",
            "-d",
            "--name",
            init,
            *self.label_args(),
            *options,
            PYTHON_IMAGE,
            "python",
            "-c",
            "import time; time.sleep(300)",
        )
        try:
            podman("cp", str(self.directory / "tls") + "/.", f"{init}:/vol/config/")
            for i in range(1, 4):
                podman("cp", self.directory / "tls/ca.pem", f"{init}:/vol/node-{i}-state/ca.pem")
            podman(
                "exec",
                init,
                "python",
                "-c",
                """
import os
from pathlib import Path
for folder in Path('/vol').iterdir():
    uid = 999 if folder.name == 'mq' else 10001
    for entry in [folder, *folder.rglob('*')]:
        os.chown(entry, uid, uid)
    if folder.name.startswith('node-'):
        work = folder / 'workspaces'
        work.mkdir(exist_ok=True)
        os.chown(work, uid, uid)
""",
            )
        finally:
            podman("rm", "-f", init)

    def build(self):
        for image, file in [
            (CONTROL_IMAGE, "deploy/compose/Dockerfile"),
            (NODE_IMAGE, "deploy/podman/Node.Dockerfile"),
        ]:
            print(f"Building {image}", flush=True)
            result = podman("build", "-t", image, "-f", file, ".", timeout=600)
            (
                self.directory / ("node-build.log" if image == NODE_IMAGE else "control-build.log")
            ).write_text(result.stdout + result.stderr)

    def start_node(self, i):
        suffix = f"node-{i}"
        self.ensure_pod(suffix)
        options = [
            "-v",
            f"{self.resource(suffix + '-state')}:/state",
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "128",
        ]
        for role, module in [
            ("agent", "node_agent.transport.client"),
        ]:
            self.container(
                f"{suffix}-{role}", suffix, NODE_IMAGE, ["python", "-m", module], suffix, options
            )

    def up(self, build=True):
        if build:
            self.build()
        network = self.resource("network")
        if not self.inspect("network", network):
            podman("network", "create", *self.label_args(), network)
        for v in ["pg", "mq", "config", *(f"node-{i}-state" for i in range(1, 4))]:
            self.ensure_volume(v)
        self.prepare_volumes()
        self.ensure_pod("control", publish=True)
        self.container(
            "postgres",
            "control",
            POSTGRES_IMAGE,
            env="postgres",
            options=["-v", f"{self.resource('pg')}:/var/lib/postgresql"],
        )
        self.container(
            "rabbitmq",
            "control",
            RABBIT_IMAGE,
            env="rabbitmq",
            options=["--user", "999:999", "-v", f"{self.resource('mq')}:/var/lib/rabbitmq"],
        )
        print("Waiting for isolated PostgreSQL / RabbitMQ", flush=True)
        wait(
            lambda: (
                podman(
                    "exec", self.resource("postgres"), "pg_isready", "-U", "platform", check=False
                ).returncode
                == 0
            ),
            "PostgreSQL",
        )
        wait(
            lambda: (
                podman(
                    "exec",
                    self.resource("rabbitmq"),
                    "rabbitmq-diagnostics",
                    "-q",
                    "ping",
                    check=False,
                ).returncode
                == 0
            ),
            "RabbitMQ",
        )
        podman(
            "run",
            "--rm",
            "--pod",
            self.resource("control"),
            *self.label_args(),
            "--env-file",
            self.directory / "control.env",
            CONTROL_IMAGE,
            "alembic",
            "upgrade",
            "head",
        )
        self.container(
            "api",
            "control",
            CONTROL_IMAGE,
            [
                "uvicorn",
                "control_plane.entrypoints.api:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8100",
                "--ssl-keyfile",
                "/config/server.key",
                "--ssl-certfile",
                "/config/server.pem",
                "--ws-max-size",
                "2097152",
            ],
            "control",
            ["-v", f"{self.resource('config')}:/config:ro"],
        )
        for role in ["scheduler", "publisher"]:
            self.container(
                role,
                "control",
                CONTROL_IMAGE,
                ["python", "-m", f"control_plane.entrypoints.{role}"],
                "control",
            )
        self.container(
            "worker",
            "control",
            CONTROL_IMAGE,
            [
                "celery",
                "-A",
                "control_plane.entrypoints.worker:app",
                "worker",
                "--pool=solo",
                "--loglevel=INFO",
            ],
            "control",
        )
        with self.client() as client:
            wait(lambda: client.get("/readyz").is_success, "HTTPS API ready")
        for i in range(1, 4):
            self.start_node(i)
        with self.client() as client:
            wait(
                lambda: (
                    len(client.get("/api/overview").json().get("devices", []))
                    == self.config["devices"] * 3
                ),
                "all node inventories",
            )
        print(
            f"Ready: https://127.0.0.1:{self.config['port']} | 3 nodes × {self.config['devices']} simulated devices",
            flush=True,
        )

    def client(self):
        return httpx.Client(
            base_url=f"https://127.0.0.1:{self.config['port']}",
            verify=ssl.create_default_context(cafile=str(self.directory / "tls/ca.pem")),
            timeout=15,
            trust_env=False,
        )

    def restart(self, suffix):
        name = self.resource(suffix)
        if not self.inspect("container", name):
            raise RuntimeError(f"Missing lab container: {name}")
        podman("restart", "--time", "2", name)

    def down(self):
        # Check ownership before any removal. Persistent data and secrets are retained.
        names = [self.resource(f"node-{i}") for i in range(1, 4)] + [self.resource("control")]
        owned = [name for name in names if self.inspect("pod", name)]
        logs = self.directory / "logs"
        logs.mkdir(mode=0o700, exist_ok=True)
        for suffix in [
            "api",
            "scheduler",
            "publisher",
            "worker",
            "postgres",
            "rabbitmq",
            *(f"node-{i}-{role}" for i in range(1, 4) for role in ["agent"]),
        ]:
            name = self.resource(suffix)
            if self.inspect("container", name):
                result = podman("logs", name, check=False)
                (logs / f"{suffix}.log").write_text(result.stdout + result.stderr)
        for name in owned:
            podman("pod", "rm", "-f", name)
        print(f"Stopped only {self.name}; data retained in its volumes and {self.directory}")

    def status(self):
        for suffix in ["control", "node-1", "node-2", "node-3"]:
            value = self.inspect("pod", self.resource(suffix))
            print(f"{self.resource(suffix)}: {value.get('State') if value else 'not created'}")
        print(f"API https://127.0.0.1:{self.config['port']} | CA {self.directory / 'tls/ca.pem'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["up", "down", "status", "verify"])
    parser.add_argument("--name", default="artemis-lab")
    parser.add_argument("--port", type=int, default=18100)
    parser.add_argument("--devices", type=int, default=20)
    parser.add_argument("--duration", type=float, default=45)
    parser.add_argument("--skip-build", action="store_true")
    args = parser.parse_args()
    if (
        not 1024 <= args.port <= 65535
        or not 1 <= args.devices <= 20
        or not 30 <= args.duration <= 300
    ):
        parser.error("port 1024..65535, devices 1..20 per node, duration 30..300 seconds")
    lab = Lab(args.name)
    if args.action == "up":
        lab.initialize(args.port, args.devices, args.duration)
        lab.up(build=not args.skip_build)
    elif not lab.config:
        parser.error("Lab not initialized; run up first")
    elif args.action == "verify":
        from pod_lab_verify import verify

        verify(lab)
    else:
        getattr(lab, args.action)()


if __name__ == "__main__":
    main()
