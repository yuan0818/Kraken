"""Fetch the pinned, official scrcpy video server (kept out of Git)."""

import hashlib
import urllib.request
from pathlib import Path

VERSION = "3.3.3"
SHA256 = "7e70323ba7f259649dd4acce97ac4fefbae8102b2c6d91e2e7be613fd5354be0"


def main():
    target = Path(__file__).resolve().parents[1] / ".runtime/tools" / f"scrcpy-server-v{VERSION}"
    target.parent.mkdir(parents=True, exist_ok=True)
    data = urllib.request.urlopen(
        f"https://github.com/Genymobile/scrcpy/releases/download/v{VERSION}/scrcpy-server-v{VERSION}",
        timeout=60,
    ).read()
    if hashlib.sha256(data).hexdigest() != SHA256:
        raise ValueError("scrcpy server checksum mismatch")
    target.write_bytes(data)
    print(target)


if __name__ == "__main__":
    main()
