"""Create a private master key once. Back it up separately from PostgreSQL; never print it."""

import argparse
import os
from pathlib import Path

from cryptography.fernet import Fernet


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path")
    path = Path(parser.parse_args().path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise SystemExit("Key file already exists; it was not changed") from None
    with os.fdopen(fd, "wb") as stream:
        stream.write(Fernet.generate_key() + b"\n")
        stream.flush()
        os.fsync(stream.fileno())
    print("Private master key created. Back up this file separately; do not commit it.")


if __name__ == "__main__":
    main()
