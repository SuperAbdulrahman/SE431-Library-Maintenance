"""Regression tests against PostgreSQL; use a disposable test database."""

from datetime import datetime, timedelta
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import MagicMock, patch
from uuid import uuid4

import psycopg
from psycopg import sql
from psycopg.rows import dict_row

from src import database, library
from src.migrate import migrate


class FineTests(unittest.TestCase):
    def test_full_overdue_days(self):
        due = datetime(2026, 1, 15, 12)
        self.assertEqual(library.calculate_fine(due, due + timedelta(days=3)), 3)
        self.assertEqual(library.calculate_fine(due, due + timedelta(hours=23)), 0)
        self.assertEqual(library.calculate_fine(due, due - timedelta(days=1)), 0)


class BusinessRuleTests(unittest.TestCase):
    def setUp(self):
        self.connection = MagicMock()
        self.today = datetime(2026, 1, 1, 12)
        patcher = patch("src.library.database")
        self.db = patcher.start()
        self.addCleanup(patcher.stop)
        self.db.get_member.return_value = {"status": "ACTIVE"}
        self.db.count_active_loans.return_value = 0
        self.db.get_book.return_value = {"available": 20}
        self.db.decrease_availability.return_value = True

    def test_active_borrow_creates_14_day_loan(self):
        due = library.borrow(self.connection, "S1", 1, self.today)
        self.assertEqual(due, self.today + timedelta(days=14))
        self.db.create_loan.assert_called_once_with(self.connection, "S1", 1, self.today, due)
        self.db.decrease_availability.assert_called_once_with(self.connection, 1)
        self.connection.transaction.assert_called_once()
        self.connection.close.assert_not_called()

    def test_rejected_borrow_does_not_write(self):
        cases = [
            ("get_member", None, "Member not found"),
            ("get_member", {"status": "INACTIVE"}, "not active"),
            ("count_active_loans", 3, "limit"),
            ("get_book", None, "Book not found"),
            ("get_book", {"available": 0}, "unavailable"),
            ("get_book", {"available": -1}, "unavailable"),
        ]
        for method, value, message in cases:
            with self.subTest(message=message):
                mock = getattr(self.db, method)
                original = mock.return_value
                mock.return_value = value
                with self.assertRaisesRegex(ValueError, message):
                    library.borrow(self.connection, "S1", 1, self.today)
                self.db.create_loan.assert_not_called()
                self.db.decrease_availability.assert_not_called()
                mock.return_value = original

    def test_no_loan_if_last_copy_taken(self):
        self.db.decrease_availability.return_value = False
        with self.assertRaisesRegex(ValueError, "unavailable"):
            library.borrow(self.connection, "S1", 1, self.today)
        self.db.create_loan.assert_not_called()

    def test_return_fines_and_updates(self):
        self.db.get_active_loan.return_value = {"id": 7, "due_date": self.today}
        for days, fine in ((-1, 0), (0, 0), (3, 3)):
            with self.subTest(days=days):
                self.assertEqual(library.return_book(self.connection, "S1", 1,
                                 self.today + timedelta(days=days)), fine)
        self.db.close_loan.assert_called_with(self.connection, 7)
        self.db.increase_availability.assert_called_with(self.connection, 1)

    def test_return_requires_active_loan(self):
        self.db.get_active_loan.return_value = None
        with self.assertRaisesRegex(ValueError, "No active loan"):
            library.return_book(self.connection, "S1", 1, self.today)
        self.db.close_loan.assert_not_called()
        self.db.increase_availability.assert_not_called()

    def test_search_returns_records_for_cli(self):
        self.db.find_books.return_value = [{"id": 1, "available": 20}]
        self.assertEqual(library.search_books(self.connection, "Clean"),
                         [{"id": 1, "available": 20}])
        self.db.find_books.assert_called_once_with(self.connection, "Clean")

    def test_write_error_exits_transaction_with_error(self):
        self.db.create_loan.side_effect = RuntimeError("write failed")
        with self.assertRaises(RuntimeError):
            library.borrow(self.connection, "S1", 1, self.today)
        exit_call = self.connection.transaction.return_value.__exit__.call_args
        self.assertIs(exit_call.args[0], RuntimeError)


@unittest.skipUnless(os.environ.get("TEST_DATABASE_URL"), "Set TEST_DATABASE_URL for PostgreSQL tests")
class LibraryTests(unittest.TestCase):
    def setUp(self):
        self.connection = psycopg.connect(os.environ["TEST_DATABASE_URL"],
                                          autocommit=True, row_factory=dict_row)
        self.schema = "test_" + uuid4().hex
        self.connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.cleanup_database)
        database.setup(self.connection)
        database.seed_demo_data(self.connection)
        self.today = datetime(2026, 1, 1, 12)

    def cleanup_database(self):
        self.connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
        self.connection.close()

    def borrow(self, member="S1", book=1):
        return library.borrow(self.connection, member, book, self.today)

    def test_tc01_active_member(self):
        self.borrow()
        self.assertEqual(database.count_active_loans(self.connection, "S1"), 1)

    def test_tc02_inactive_member(self):
        self.connection.execute("UPDATE members SET status = 'INACTIVE' WHERE id = 'S1'")
        with self.assertRaisesRegex(ValueError, "not active"):
            self.borrow()
        self.assertEqual(database.count_active_loans(self.connection, "S1"), 0)

    def test_tc03_three_loan_limit(self):
        for _ in range(3):
            self.borrow()
        with self.assertRaisesRegex(ValueError, "limit"):
            self.borrow()
        self.assertEqual(database.count_active_loans(self.connection, "S1"), 3)
        self.assertEqual(database.get_book(self.connection, 1)["available"], 17)

    def test_tc04_due_date(self):
        self.assertEqual(self.borrow(), self.today + timedelta(days=14))

    def test_tc05_late_return(self):
        self.borrow()
        fine = library.return_book(self.connection, "S1", 1, self.today + timedelta(days=17))
        self.assertEqual(fine, 3)
        self.assertEqual(database.get_book(self.connection, 1)["available"], 20)

    def test_tc06_on_time_return(self):
        self.borrow()
        self.assertEqual(library.return_book(self.connection, "S1", 1,
                                             self.today + timedelta(days=14)), 0)

    def test_tc07_search_title_or_author(self):
        for keyword in ("clean", "MARTIN"):
            self.assertTrue(any(book["id"] == 1 for book in
                                library.search_books(self.connection, keyword)))
        self.assertEqual(library.search_books(self.connection, "unknown"), [])

    def test_tc08_no_active_loan(self):
        with self.assertRaisesRegex(ValueError, "No active loan"):
            library.return_book(self.connection, "S1", 1, self.today)
        self.assertEqual(database.get_book(self.connection, 1)["available"], 20)

    def test_tc09_zero_copies(self):
        self.connection.execute("UPDATE books SET available = 0 WHERE id = 1")
        with self.assertRaisesRegex(ValueError, "unavailable"):
            self.borrow()

    def test_tc10_quantity_decreases(self):
        self.borrow()
        self.assertEqual(database.get_book(self.connection, 1)["available"], 19)

    def test_missing_member_and_book(self):
        with self.assertRaisesRegex(ValueError, "Member not found"):
            self.borrow(member="missing")
        with self.assertRaisesRegex(ValueError, "Book not found"):
            self.borrow(book=999)

    def test_borrow_rollback(self):
        with patch.object(database, "create_loan", side_effect=RuntimeError("write failed")):
            with self.assertRaises(RuntimeError):
                self.borrow()
        self.assertEqual(database.get_book(self.connection, 1)["available"], 20)
        self.assertEqual(database.count_active_loans(self.connection, "S1"), 0)
        self.borrow()  # Connection is still usable after rollback.

    def test_return_rollback(self):
        self.borrow()
        with patch.object(database, "increase_availability", side_effect=RuntimeError("write failed")):
            with self.assertRaises(RuntimeError):
                library.return_book(self.connection, "S1", 1, self.today)
        self.assertEqual(database.count_active_loans(self.connection, "S1"), 1)
        self.assertEqual(database.get_book(self.connection, 1)["available"], 19)

    def test_repeated_return_does_not_add_copies(self):
        self.borrow()
        library.return_book(self.connection, "S1", 1, self.today)
        with self.assertRaises(ValueError):
            library.return_book(self.connection, "S1", 1, self.today)
        self.assertEqual(database.get_book(self.connection, 1)["available"], 20)

    def test_seed_does_not_reset_quantities(self):
        self.borrow()
        database.seed_demo_data(self.connection)
        self.assertEqual(database.get_book(self.connection, 1)["available"], 19)

    def make_legacy_database(self, directory, invalid_member=False):
        path = Path(directory) / "library.db"
        with sqlite3.connect(path) as source:
            source.executescript("""
                CREATE TABLE members (id TEXT PRIMARY KEY, name TEXT, status TEXT);
                CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, author TEXT, available INTEGER);
                CREATE TABLE loans (id INTEGER PRIMARY KEY, member_id TEXT, book_id INTEGER,
                                    borrow_date TEXT, due_date TEXT, returned INTEGER);
                INSERT INTO members VALUES ('S1', 'Ali', 'ACTIVE');
                INSERT INTO books VALUES (1, 'Clean Code', 'Robert Martin', 0);
                INSERT INTO loans VALUES (7, 'S1', 1, '2026-01-01T12:00:00', '2026-01-15T12:00:00', 0);
                INSERT INTO loans VALUES (8, 'S1', 1, '2025-12-01T12:00:00', '2025-12-15T12:00:00', 1);
            """)
            if invalid_member:
                source.execute("UPDATE loans SET member_id = 'missing'")
        return path

    def test_migration_preserves_records_and_sequence(self):
        self.connection.execute("TRUNCATE loans, books, members RESTART IDENTITY")
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_legacy_database(directory)
            backup = migrate(source, self.connection)
            self.assertTrue(backup.exists())
            self.assertEqual(self.connection.execute("SELECT COUNT(*) AS n FROM loans").fetchone()["n"], 2)
            self.assertEqual(database.get_book(self.connection, 1)["available"], 0)
            self.assertEqual(library.return_book(self.connection, "S1", 1,
                                                 self.today + timedelta(days=17)), 3)
            self.borrow()
            self.assertEqual(self.connection.execute("SELECT MAX(id) AS id FROM loans").fetchone()["id"], 9)
            with self.assertRaises(FileExistsError):
                migrate(source, self.connection)

    def test_migration_rejects_nonempty_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_legacy_database(directory)
            with self.assertRaisesRegex(ValueError, "empty"):
                migrate(source, self.connection)
        self.assertEqual(database.get_book(self.connection, 1)["available"], 20)

    def test_migration_invalid_relationship_rolls_back(self):
        self.connection.execute("TRUNCATE loans, books, members RESTART IDENTITY")
        with tempfile.TemporaryDirectory() as directory:
            source = self.make_legacy_database(directory, invalid_member=True)
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                migrate(source, self.connection)
        self.assertIsNone(database.get_book(self.connection, 1))
        self.assertIsNone(database.get_member(self.connection, "S1"))


if __name__ == "__main__":
    unittest.main()
