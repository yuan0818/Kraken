from pathlib import Path

from alembic import op

revision = "0009"
down_revision = "0008"


def upgrade():
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("Restore coordinated backup to retain control leases")
