from pathlib import Path

from alembic import op

revision = "0001"
down_revision = None


def upgrade():
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("M0 schema rollback requires restoring the pre-migration backup")
