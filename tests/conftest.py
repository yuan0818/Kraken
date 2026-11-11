import os
from pathlib import Path
from uuid import uuid4

import pytest
from control_plane.infrastructure import db
from sqlalchemy import create_engine


@pytest.fixture
def database(monkeypatch):
    url = os.getenv("TEST_DATABASE_URL") or db.engine.url
    schema = "test_" + uuid4().hex
    admin = create_engine(url)
    with admin.begin() as conn:
        conn.exec_driver_sql(f"CREATE SCHEMA {schema}")
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    with engine.begin() as conn:
        for path in sorted(
            (Path(__file__).resolve().parents[1] / "migrations/versions").glob("*.sql")
        ):
            conn.exec_driver_sql(path.read_text())
    monkeypatch.setattr(db, "engine", engine)
    yield engine
    engine.dispose()
    with admin.begin() as conn:
        conn.exec_driver_sql(f"DROP SCHEMA {schema} CASCADE")
    admin.dispose()
