from pathlib import Path

from alembic import op

revision = "0005"
down_revision = "0004"


def upgrade():
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("Task history rollback requires a pre-migration backup")
