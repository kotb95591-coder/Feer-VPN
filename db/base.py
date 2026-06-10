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


def _build_engine_config() -> tuple[str, dict]:
    """Собираем (URL, connect_args) для SQLAlchemy.

    ВАЖНО (фикс WSServerHandshakeError 400):
    sqlalchemy-libsql 0.2.x ходит в Turso по HTTP через Rust-клиент
    libsql_experimental. Старая 0.1.0 умела ТОЛЬКО WebSocket (wss://),
    а Turso на региональных хостах (*.aws-eu-west-1.turso.io) отвечает на
    WS-handshake кодом 400. Поэтому:
      - URL: sqlite+libsql://<host>?secure=true   (secure=true => https, не wss)
      - токен передаём через connect_args["auth_token"], а НЕ в query-строке.

    Если Turso задан и драйвер есть — идём в Turso.
    Иначе (например локальный тест на новом Python) — локальный SQLite-файл.
    """
    if config.TURSO_DATABASE_URL and _libsql_ready():
        host = (
            config.TURSO_DATABASE_URL
            .replace("libsql://", "")
            .replace("https://", "")
            .replace("wss://", "")
            .rstrip("/")
        )
        url = f"sqlite+libsql://{host}?secure=true"
        connect_args = {
            "auth_token": config.TURSO_AUTH_TOKEN,
            "check_same_thread": False,
        }
        return url, connect_args
    if config.TURSO_DATABASE_URL:
        log.warning(
            "TURSO задан, но драйвер libSQL не установлен — использую локальный SQLite %s "
            "(ОК для теста; на Vercel с Python 3.12 будет Turso)",
            config.LOCAL_DB_PATH,
        )
    else:
        log.warning("TURSO_DATABASE_URL не задан — использую локальный SQLite (%s)", config.LOCAL_DB_PATH)
    return f"sqlite:///{config.LOCAL_DB_PATH}", {"check_same_thread": False}


_db_url, _connect_args = _build_engine_config()

engine = create_engine(
    _db_url,
    echo=False,
    future=True,
    pool_pre_ping=True,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def _safe_migrate() -> None:
    """Лёгкие миграции для уже существующих таблиц.

    create_all() добавляет недостающие ТАБЛИЦЫ, но не добавляет новые КОЛОНКИ
    в существующие. Поэтому новые поля (например users.balance) добавляем
    через ALTER TABLE вручную. Каждый ALTER — в своей транзакции; если колонка
    уже есть, ошибку просто игнорируем.
    """
    from sqlalchemy import text

    statements = [
        "ALTER TABLE users ADD COLUMN balance FLOAT DEFAULT 0",
        "ALTER TABLE promocodes ADD COLUMN percent FLOAT DEFAULT 0",
        "ALTER TABLE promocodes ADD COLUMN fixed_price FLOAT DEFAULT 0",
        "ALTER TABLE promocodes ADD COLUMN bonus_days INTEGER DEFAULT 0",
        "ALTER TABLE promocodes ADD COLUMN target_plan VARCHAR(16) DEFAULT 'all'",
        "ALTER TABLE users ADD COLUMN trial_used BOOLEAN DEFAULT 0",
    ]
    for stmt in statements:
        try:
            with engine.begin() as conn:
                conn.execute(text(stmt))
            log.info("Миграция применена: %s", stmt)
        except Exception as e:  # noqa: BLE001
            log.debug("Миграция пропущена (%s): %s", stmt, e)


def init_db() -> None:
    """Создаёт таблицы, если их нет."""
    from db import models  # noqa: F401  (регистрируем модели)

    Base.metadata.create_all(engine)
    _safe_migrate()
    log.info("БД инициализирована")
