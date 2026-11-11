from pathlib import Path

from alembic import op

revision = "0008"
down_revision = "0007"


def upgrade():
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("Restore a coordinated backup to retain physical execution history")
