from pathlib import Path

from alembic import op

revision = "0007"
down_revision = "0006"


def upgrade():
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("Restore a coordinated backup to retain observed devices")
