from pathlib import Path

from alembic import op

revision = "0003"
down_revision = "0002"


def upgrade():
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("Case metadata rollback requires a pre-migration backup")
