"""Run native development services; Ctrl-C stops only children created here."""

import os
import signal
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PYTHON = str(ROOT / ".venv/bin/python")


def environment():
    from dotenv import dotenv_values

    return {
        **{k: v for k, v in dotenv_values(ROOT / "config/dev.env.example").items() if v},
        **{k: v for k, v in dotenv_values(ROOT / ".env").items() if v is not None},
        **os.environ,
    }


COMMANDS = {
    "api": [
        PYTHON,
        "-m",
        "uvicorn",
        "control_plane.entrypoints.api:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8100",
        "--ws-max-size",
        "2097152",
    ],
    "scheduler": [PYTHON, "-m", "control_plane.entrypoints.scheduler"],
    "publisher": [PYTHON, "-m", "control_plane.entrypoints.publisher"],
    "worker": [
        PYTHON,
        "-m",
        "celery",
        "-A",
        "control_plane.entrypoints.worker:app",
        "worker",
        "--pool=solo",
        "--loglevel=INFO",
        "--hostname=m0@%h",
    ],
    "node": [PYTHON, "-m", "node_agent.transport.client"],
    "web": ["pnpm", "--filter", "@kraken/web", "dev"],
}


class Processes:
    def __init__(self, env=None):
        self.env = env or environment()
        self.children = {}
        self.logs = []
        (ROOT / ".runtime/logs").mkdir(parents=True, exist_ok=True)

    def start(self, name):
        if name in self.children and self.children[name].poll() is None:
            raise RuntimeError(f"{name} already running")
        log = (ROOT / f".runtime/logs/{name}.log").open("a")
        self.logs.append(log)
        self.children[name] = subprocess.Popen(
            COMMANDS[name],
            cwd=ROOT,
            env=self.env,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        print(f"{name}: pid={self.children[name].pid}", flush=True)

    def stop(self, name, crash=False):
        p = self.children.get(name)
        if p and p.poll() is None:
            os.killpg(p.pid, signal.SIGKILL if crash else signal.SIGTERM)
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(p.pid, signal.SIGKILL)
                p.wait()

    def close(self):
        for name in reversed(self.children):
            self.stop(name)
        for log in self.logs:
            log.close()


def main():
    stack = Processes()

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        for name in COMMANDS:
            stack.start(name)
        print(
            "Web http://127.0.0.1:5178 | API http://127.0.0.1:8100/docs | logs .runtime/logs",
            flush=True,
        )
        while True:
            for name, p in stack.children.items():
                if p.poll() is not None:
                    raise RuntimeError(f"{name} exited; see .runtime/logs/{name}.log")
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stack.close()


if __name__ == "__main__":
    main()
