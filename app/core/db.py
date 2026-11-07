from collections.abc import Generator
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


@lru_cache
def get_engine() -> Engine:
    return create_engine(settings().database_url)


def get_db() -> Generator[Session, None, None]:
    session = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)()
    try:
        yield session
    finally:
        session.close()
