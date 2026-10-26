"""Einfaches, versioniertes Migrationssystem (laufen beim Start automatisch).

Neue Schemaänderungen: neue Funktion `m000X_...` schreiben und unten in MIGRATIONS eintragen
(idempotentes SQL, z. B. ALTER TABLE ... ADD COLUMN IF NOT EXISTS). Migration 0001 ist die Baseline.
"""
import logging

from sqlalchemy import text

from .db import Base
from . import models  # noqa: F401  (registriert Tabellen)

log = logging.getLogger("kombi.migrations")
LOCK_ID = 727274


def m0001_baseline(conn):
    Base.metadata.create_all(conn)


MIGRATIONS = [("0001_baseline", m0001_baseline)]


def run(engine):
    lock_conn = None
    if engine.dialect.name == "postgresql":  # mehrere Replikas starten sicher nacheinander
        lock_conn = engine.connect()
        lock_conn.execute(text("SELECT pg_advisory_lock(:i)"), {"i": LOCK_ID})
    try:
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations "
                              "(version VARCHAR(64) PRIMARY KEY, applied_at TIMESTAMP)"))
            done = {r[0] for r in conn.execute(text("SELECT version FROM schema_migrations"))}
            for version, fn in MIGRATIONS:
                if version in done:
                    continue
                log.info("Migration %s", version)
                fn(conn)
                conn.execute(text("INSERT INTO schema_migrations(version, applied_at) VALUES (:v, CURRENT_TIMESTAMP)"),
                             {"v": version})
    finally:
        if lock_conn is not None:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:i)"), {"i": LOCK_ID})
            lock_conn.commit()
            lock_conn.close()
