from pathlib import Path

from alembic import op

revision = "0006"
down_revision = "0005"


def upgrade():
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("Model and credential history requires restoring a coordinated backup")
