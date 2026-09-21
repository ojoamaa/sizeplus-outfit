import os
import sqlite3

import psycopg
from psycopg.rows import dict_row


SQLITE_DB = "sizeplus_pre_postgres_migration.db"
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

TABLES = [
    "users",
    "sessions",
    "products",
    "variants",
    "product_images",
    "customers",
    "sales",
    "sale_items",
    "stock_movements",
    "orders",
    "order_items",
    "audit_logs",
]

IDENTITY_TABLES = [
    "users",
    "products",
    "variants",
    "product_images",
    "customers",
    "sales",
    "sale_items",
    "stock_movements",
    "orders",
    "order_items",
    "audit_logs",
]


def main():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured.")

    if not os.path.exists(SQLITE_DB):
        raise RuntimeError(f"SQLite backup not found: {SQLITE_DB}")

    sqlite_conn = sqlite3.connect(SQLITE_DB)
    sqlite_conn.row_factory = sqlite3.Row

    pg_conn = psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row,
    )

    try:
        print("SIZEPLUS SQLITE -> POSTGRESQL MIGRATION")
        print("=" * 52)

        # Safety gate: PostgreSQL must still be empty.
        for table in TABLES:
            with pg_conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
                if cur.fetchone()["n"] != 0:
                    raise RuntimeError(
                        f"Target table {table} is not empty. "
                        "Migration stopped."
                    )

        migrated_total = 0

        for table in TABLES:
            rows = sqlite_conn.execute(
                f"SELECT * FROM {table}"
            ).fetchall()

            if not rows:
                print(f"{table:18} migrated=0")
                continue

            columns = rows[0].keys()
            column_sql = ", ".join(columns)
            placeholders = ", ".join(["%s"] * len(columns))

            sql = (
                f"INSERT INTO {table} "
                f"({column_sql}) VALUES ({placeholders})"
            )

            with pg_conn.cursor() as cur:
                for row in rows:
                    cur.execute(
                        sql,
                        tuple(row[column] for column in columns),
                    )

            migrated_total += len(rows)
            print(f"{table:18} migrated={len(rows)}")

        # Reset PostgreSQL identity sequences after preserving SQLite IDs.
        with pg_conn.cursor() as cur:
            for table in IDENTITY_TABLES:
                cur.execute(
                    """
                    SELECT setval(
                        pg_get_serial_sequence(%s, 'id'),
                        COALESCE((SELECT MAX(id) FROM """
                    + table
                    + """), 1),
                        EXISTS(SELECT 1 FROM """
                    + table
                    + """)
                    )
                    """,
                    (table,),
                )

        # Verify all table counts before commit.
        verified_total = 0

        for table in TABLES:
            source_count = sqlite_conn.execute(
                f"SELECT COUNT(*) AS n FROM {table}"
            ).fetchone()["n"]

            with pg_conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
                target_count = cur.fetchone()["n"]

            if source_count != target_count:
                raise RuntimeError(
                    f"Verification failed for {table}: "
                    f"SQLite={source_count}, PostgreSQL={target_count}"
                )

            verified_total += target_count

        if migrated_total != 39 or verified_total != 39:
            raise RuntimeError(
                f"Total verification failed: "
                f"migrated={migrated_total}, verified={verified_total}"
            )

        pg_conn.commit()

        print("=" * 52)
        print("MIGRATION COMMITTED")
        print("Migrated records:", migrated_total)
        print("Verified records:", verified_total)

    except Exception:
        pg_conn.rollback()
        print()
        print("MIGRATION ROLLED BACK")
        raise

    finally:
        sqlite_conn.close()
        pg_conn.close()


if __name__ == "__main__":
    main()