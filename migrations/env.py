from alembic import context
from control_plane.infrastructure.db import engine

with engine.connect() as connection:
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()
