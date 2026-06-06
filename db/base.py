"""Подключение к БД. Turso (libSQL) в проде, локальный SQLite для тестов."""
import logging

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker

from config import config

log = logging.getLogger(__name__)

Base = declarative_base()


def _libsql_ready() -> bool:
    """Есть ли рабочий драйвер libSQL (нужен для Turso).

    На свежих версиях Python (3.14+) для libsql-experimental ещё нет wheels,
    поэтому локально он может быть не установлен — тогда откатываемся на SQLite.
    """
    try:
        import libsql_experimental  # noqa: F401
        import sqlalchemy_libsql  # noqa: F401
        return True
    except Exception as e:  # pragma: no cover
        log.warning("libSQL драйвер недоступен (%s)", e)
        return False


def _build_url() -> str:
    """Собираем SQLAlchemy URL.

    Turso отдаёт адрес вида libsql://<db>-<org>.turso.io.
    Диалект sqlalchemy-libsql ожидает sqlite+libsql://<host>/?authToken=...&secure=true

    Если Turso задан и драйвер есть — идём в Turso.
    Иначе (например локальный тест на Python 3.14) — локальный SQLite-файл.
    """
    if config.TURSO_DATABASE_URL and _libsql_ready():
        host = (
            config.TURSO_DATABASE_URL
            .replace("libsql://", "")
            .replace("https://", "")
            .replace("wss://", "")
            .rstrip("/")
        )
        token = config.TURSO_AUTH_TOKEN
        return f"sqlite+libsql://{host}/?authToken={token}&secure=true"
    if config.TURSO_DATABASE_URL:
        log.warning(
            "TURSO задан, но драйвер libSQL не установлен — использую локальный SQLite %s "
            "(ОК для теста; на Vercel с Python 3.12 будет Turso)",
            config.LOCAL_DB_PATH,
        )
    else:
        log.warning("TURSO_DATABASE_URL не задан — использую локальный SQLite (%s)", config.LOCAL_DB_PATH)
    return f"sqlite:///{config.LOCAL_DB_PATH}"


engine = create_engine(
    _build_url(),
    echo=False,
    future=True,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def init_db() -> None:
    """Создаёт таблицы, если их нет."""
    from db import models  # noqa: F401  (регистрируем модели)

    Base.metadata.create_all(engine)
    log.info("БД инициализирована")
