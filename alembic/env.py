from alembic import context

from app.core.config import settings
from app.core.db import Base, get_engine
from app.jobs import models  # noqa: F401 — registers tables on Base.metadata
from app.capabilities import models as capability_models  # noqa: F401
from app import provider  # noqa: F401 — registers AI usage ledger


def migrate(connection):
    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
        compare_type=True,
        render_as_batch=connection.dialect.name == "sqlite",
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    context.configure(
        url=settings().database_url,
        target_metadata=Base.metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
elif context.config.attributes.get("connection") is not None:
    migrate(context.config.attributes["connection"])
else:
    with get_engine().connect() as connection:
        migrate(connection)
