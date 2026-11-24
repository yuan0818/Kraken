"""Encrypted credential storage; the master key is provisioned separately from the database."""

import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


class SecretStoreUnavailable(Exception):
    pass


def cipher():
    path = os.getenv("PLATFORM_SECRET_KEY_FILE")
    try:
        if not path:
            raise ValueError
        keyfile = Path(path)
        if keyfile.stat().st_mode & 0o077:
            raise ValueError
        return Fernet(keyfile.read_bytes().strip())
    except (OSError, ValueError):
        raise SecretStoreUnavailable("凭据存储不可用：请配置权限为 0600 的平台主密钥文件") from None


def encrypt(value):
    return cipher().encrypt(value.encode()).decode()


def decrypt(value):
    try:
        return cipher().decrypt(value.encode()).decode()
    except InvalidToken:
        raise SecretStoreUnavailable("凭据无法解密：请恢复与数据库配套的平台主密钥") from None
