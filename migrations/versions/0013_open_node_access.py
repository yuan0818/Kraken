from pathlib import Path

from alembic import op

revision = "0013"
down_revision = "0012"


def upgrade():
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("Restore a backup before re-enabling node authentication")
