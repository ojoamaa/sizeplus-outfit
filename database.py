import os
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
SQLITE_DB = os.path.join(BASE, "sizeplus.db")

DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


def database_backend():
    if DATABASE_URL:
        return "postgresql"
    return "sqlite"


def conn():
    """
    Return a database connection.

    V0.7 migration stage:
    - Local development continues using SQLite.
    - PostgreSQL will be enabled after the SQL compatibility
      layer is introduced and tested.
    """
    if DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is configured, but PostgreSQL mode "
            "has not yet been activated in this migration stage."
        )

    connection = sqlite3.connect(SQLITE_DB)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection