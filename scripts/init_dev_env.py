"""Generate local development credentials once without printing their values."""

import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    path = ROOT / ".env"
    password = secrets.token_hex(24)
    broker_password = secrets.token_hex(24)
    values = {
        "POSTGRES_PASSWORD": password,
        "RABBITMQ_PASSWORD": broker_password,
        "DATABASE_URL": f"postgresql+psycopg://platform:{password}@127.0.0.1:56432/platform",
        "BROKER_URL": f"amqp://platform:{broker_password}@127.0.0.1:56732/platform",
    }
    content = (ROOT / "config/dev.env.example").read_text()
    for name, value in values.items():
        content = content.replace(f"{name}=\n", f"{name}={value}\n")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit(
            "Local .env already exists. Existing credentials were preserved."
        ) from None
    with os.fdopen(descriptor, "w") as stream:
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    print("Created private development configuration in .env.")


if __name__ == "__main__":
    main()
