"""Back up and migrate a legacy SQLite database into an empty PostgreSQL database."""

import argparse
from datetime import datetime
from pathlib import Path
import sqlite3

from . import database

TABLE_COLUMNS = {
    "members": ("id", "name", "status"),
    "books": ("id", "title", "author", "available"),
    "loans": ("id", "member_id", "book_id", "borrow_date", "due_date", "returned"),
}


def migrate(source_path, connection):
    source_path = Path(source_path).resolve(strict=True)
    backup_path = source_path.with_name(source_path.name + ".backup")
    # Never overwrite an earlier backup.
    with backup_path.open("xb"):
        pass
    source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
    try:
        with sqlite3.connect(backup_path) as backup:
            source.backup(backup)
        # Read the consistent backup, including any committed SQLite WAL data.
        with sqlite3.connect(backup_path) as snapshot:
            rows = {
                table: snapshot.execute(
                    f"SELECT {', '.join(columns)} FROM {table} ORDER BY id"
                ).fetchall()
                for table, columns in TABLE_COLUMNS.items()
            }
        rows["loans"] = [
            (*row[:3], datetime.fromisoformat(row[3]),
             datetime.fromisoformat(row[4]), row[5]) for row in rows["loans"]
        ]
        with connection.transaction():
            database.setup(connection)
            for table in TABLE_COLUMNS:
                if connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone():
                    raise ValueError("Migration requires empty destination tables")
            for table, columns in TABLE_COLUMNS.items():
                placeholders = ", ".join(["%s"] * len(columns))
                for row in rows[table]:
                    connection.execute(
                        f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                        row,
                    )
                migrated = connection.execute(
                    f"SELECT {', '.join(columns)} FROM {table} ORDER BY id"
                ).fetchall()
                if [tuple(row.values()) for row in migrated] != rows[table]:
                    raise ValueError(f"Migration validation failed for {table}")
            # Foreign keys validate relationships; restore generated loan IDs too.
            connection.execute("""
                SELECT setval(pg_get_serial_sequence('loans', 'id'),
                              COALESCE(MAX(id), 1), COUNT(*) > 0) FROM loans
            """)
    finally:
        source.close()
    return backup_path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("sqlite_file")
    args = parser.parse_args()
    with database.connect() as connection:
        backup = migrate(args.sqlite_file, connection)
    print(f"Migration complete. SQLite backup: {backup}")


if __name__ == "__main__":
    main()
