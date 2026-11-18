import os

from alembic import context
from sqlalchemy import create_engine, pool
from sqlalchemy.engine import make_url

url = make_url(os.environ["DATABASE_URL"]).set(drivername="postgresql+psycopg")

if context.is_offline_mode():
    context.configure(url=url, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(url, poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
