from pathlib import Path

from alembic import op

revision = "0002"
down_revision = "0001"


def upgrade():
    op.get_bind().exec_driver_sql(Path(__file__).with_suffix(".sql").read_text())


def downgrade():
    raise RuntimeError("Drain active attempts before restoring the pre-migration backup")
