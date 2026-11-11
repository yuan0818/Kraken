import os
from pathlib import Path

from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).resolve().parents[5] / ".env", override=False)

engine = create_engine(
    os.getenv("DATABASE_URL", "postgresql+psycopg://127.0.0.1:56432/platform"),
    pool_pre_ping=True,
)


def one(conn, sql, **params):
    return conn.execute(text(sql), params).mappings().one_or_none()


def rows(conn, sql, **params):
    return [dict(row) for row in conn.execute(text(sql), params).mappings()]


def execute(conn, sql, **params):
    return conn.execute(text(sql), params)
