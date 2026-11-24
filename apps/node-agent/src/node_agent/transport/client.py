"""Control-plane runtime client. No local command database or event cursor."""

import os
from urllib.parse import urlsplit
from uuid import uuid4

import httpx


class RuntimeClient:
    def __init__(self, client, node_id, spool):
        self.client, self.node_id, self.spool = client, node_id, spool
        self.boot_id = uuid4()
        self.generation = None
        self.online = False

    def request(self, method, route, **kwargs):
        response = self.client.request(method, f"/api/nodes/{self.node_id}{route}", **kwargs)
        response.raise_for_status()
        return response.json()

    def headers(self):
        return {"X-Node-Generation": str(self.generation)}

    def refresh(self):
        self.online = False
        if self.generation is None:
            result = self.request("POST", "/runtime", json={"boot_id": str(self.boot_id)})
            self.generation = result["generation"]
        self.flush()
        result = self.request("GET", "/runtime", headers=self.headers())
        self.online = True
        return result["jobs"]

    def flush(self):
        for path, entry in self.spool.events():
            result = self.request("POST", "/events", headers=self.headers(), json=entry["event"])
            if result["acknowledged"] != entry["event"]["event_id"]:
                raise ValueError("Event acknowledgement mismatch")
            self.spool.acknowledge(path)

    def claim(self, cmd):
        if not self.online:
            raise RuntimeError("Control plane has not acknowledged runtime recovery")
        try:
            return self.request("POST", f"/commands/{cmd.command_id}/claim", headers=self.headers())
        except httpx.HTTPError:
            self.online = False
            raise


def platform_url():
    base = os.getenv("API_URL", "http://127.0.0.1:8100").rstrip("/")
    parsed = urlsplit(base)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
    ):
        raise ValueError("API_URL must be an HTTP/HTTPS control-plane URL without credentials")
    return base


def main():
    # Keep the previous CLI entry point while running transport and supervision together.
    from node_agent.supervisor.simulator import main as simulate

    simulate()


if __name__ == "__main__":
    main()
