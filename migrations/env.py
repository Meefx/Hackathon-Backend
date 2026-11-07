"""Alembic owns DDL. Runtime repositories use psycopg directly."""

from alembic import context
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy import create_engine
from sqlalchemy.engine import URL
from sqlalchemy.pool import NullPool

from app.config import get_settings

# Libpq parsing handles percent-encoded URLs and isolated test search_path options.
parameters = conninfo_to_dict(get_settings().database_url)
url = URL.create(
    "postgresql+psycopg",
    username=parameters.pop("user", None),
    password=parameters.pop("password", None),
    host=parameters.pop("host", None),
    port=int(parameters.pop("port")) if parameters.get("port") else None,
    database=parameters.pop("dbname", None),
    query=parameters,
)

if context.is_offline_mode():
    context.configure(url=url, literal_binds=True, dialect_opts={"paramstyle": "named"})
    with context.begin_transaction():
        context.run_migrations()
else:
    with create_engine(url, poolclass=NullPool).connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
