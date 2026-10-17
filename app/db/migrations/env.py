from alembic import context

from app.config import Settings
from app.db import models  # noqa: F401
from app.db.base import Base, make_engine

config = context.config
url = Settings().database_url
target_metadata = Base.metadata

if context.is_offline_mode():
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = make_engine(url)
    with engine.connect() as connection:
        if engine.dialect.name == "sqlite":
            # Batch recreation of user must preserve existing foreign-key references.
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
        if engine.dialect.name == "sqlite":
            violations = connection.exec_driver_sql("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise RuntimeError("Migration left invalid foreign key references")
            connection.commit()
            connection.exec_driver_sql("PRAGMA foreign_keys=ON")
    engine.dispose()
