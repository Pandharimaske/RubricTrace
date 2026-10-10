"""PostgreSQL access for RubricTrace: a connection pool plus a small migration runner.

The schema lives in ``supabase/migrations/*.sql``. In production apply it with the Supabase CLI;
``Database.migrate()`` applies the same files for tests and local development. The application
never creates or alters tables itself: ``check_schema()`` fails fast at startup when the schema
is missing.

Queries live in the repository classes (db/repositories.py and db/exams.py). Every method takes
an optional open connection so several calls can share one transaction.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import psycopg
from backend.app.core.config import Settings
from psycopg.rows import dict_row
from psycopg.types.numeric import FloatLoader
from psycopg.types.string import TextLoader
from psycopg_pool import ConnectionPool

log = logging.getLogger(__name__)

Connection = psycopg.Connection[dict[str, Any]]

# <repo>/supabase/migrations (this file is <repo>/backend/app/db/database.py).
MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "supabase" / "migrations"


def _configure(conn: psycopg.Connection[Any]) -> None:
    """Hand uuid columns back as str and numeric columns as float, so rows are JSON-friendly
    and plain arithmetic works on marks."""
    conn.adapters.register_loader("uuid", TextLoader)
    conn.adapters.register_loader("numeric", FloatLoader)


class Database:
    """A lazily opened connection pool.

    ``schema`` pins every transaction to one schema (``set_config('search_path', ..., true)``).
    The test suite uses it to give each test a throwaway schema; it works through transaction
    poolers because it is transaction-local. Production leaves it unset.
    """

    def __init__(self, url: str, *, schema: str | None = None, max_connections: int = 10) -> None:
        self.url = url
        self.schema = schema
        self.max_connections = max_connections
        self._pool: ConnectionPool[Any] | None = None
        self._lock = threading.Lock()

    @classmethod
    def from_settings(cls, config: Settings) -> Database:
        return cls(config.database_url, max_connections=config.db_pool_max)

    def _get_pool(self) -> ConnectionPool[Any]:
        pool = self._pool
        if pool is not None:
            return pool
        with self._lock:
            if self._pool is None:
                if not self.url:
                    raise RuntimeError(
                        "RUBRICTRACE_DATABASE_URL is not set. RubricTrace needs a PostgreSQL "
                        "database (Supabase); apply supabase/migrations/ to it first."
                    )
                new_pool: ConnectionPool[Any] = ConnectionPool(
                    self.url,
                    min_size=1,
                    max_size=self.max_connections,
                    kwargs={
                        "row_factory": dict_row,
                        # Server-side prepared statements collide behind transaction-mode
                        # poolers such as Supabase's.
                        "prepare_threshold": None,
                    },
                    configure=_configure,
                    check=ConnectionPool.check_connection,
                    open=False,
                    name="rubrictrace",
                )
                new_pool.open(wait=True, timeout=30)
                self._pool = new_pool
            return self._pool

    def close(self) -> None:
        with self._lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            pool.close()

    @contextmanager
    def connection(self) -> Iterator[Connection]:
        """A transaction: commits on success, rolls back on error, then returns the
        connection to the pool."""
        with self._get_pool().connection() as conn:
            if self.schema:
                conn.execute("select set_config('search_path', %s, true)", (self.schema,))
            yield cast(Connection, conn)

    @contextmanager
    def use(self, conn: Connection | None = None) -> Iterator[Connection]:
        """Reuse a caller's connection (so several repository calls share one transaction),
        or open a new one."""
        if conn is not None:
            yield conn
        else:
            with self.connection() as opened:
                yield opened

    # ── Schema ───────────────────────────────────────────────────────────────

    def check_schema(self) -> None:
        """Raise a clear error when the migrations have not been applied."""
        with self.connection() as conn:
            row = conn.execute("select to_regclass('exams')::text as t").fetchone()
        if not row or row["t"] is None:
            raise RuntimeError(
                "The database has no RubricTrace schema. Apply supabase/migrations/*.sql "
                "(Supabase CLI: `supabase db push`)."
            )

    def migrate(self) -> list[str]:
        """Apply any migration file not yet recorded, in filename order. For tests and local
        databases; production applies the same files with the Supabase CLI."""
        applied_now: list[str] = []
        with self.connection() as conn:
            conn.execute(
                "create table if not exists schema_migrations ("
                "version text primary key, applied_at timestamptz not null default now())"
            )
            done = {r["version"] for r in conn.execute("select version from schema_migrations")}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.stem in done:
                    continue
                log.info("Applying migration %s", path.name)
                # No parameters, so the file is sent as one multi-statement simple query
                # (it contains $$ blocks, which must not be split on ';').
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("insert into schema_migrations (version) values (%s)", (path.stem,))
                applied_now.append(path.stem)
        return applied_now
