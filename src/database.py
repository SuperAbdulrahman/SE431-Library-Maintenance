"""PostgreSQL connections, queries and initialization."""

import os

import psycopg
from psycopg.rows import dict_row

from .constraints import INITIAL_BOOK_COPIES


def connect():
    # Explicit transactions below keep the connection reusable between CLI actions.
    return psycopg.connect(
        os.environ["DATABASE_URL"], autocommit=True, row_factory=dict_row
    )


def setup(connection):
    with connection.transaction():
        connection.execute("""
            CREATE TABLE IF NOT EXISTS members (
                id TEXT PRIMARY KEY, name TEXT, status TEXT
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS books (
                id INTEGER PRIMARY KEY, title TEXT, author TEXT,
                available INTEGER NOT NULL CHECK (available >= 0)
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS loans (
                id SERIAL PRIMARY KEY,
                member_id TEXT REFERENCES members(id),
                book_id INTEGER REFERENCES books(id),
                borrow_date TIMESTAMP, due_date TIMESTAMP,
                returned INTEGER NOT NULL CHECK (returned IN (0, 1))
            )
        """)


def seed_demo_data(connection):
    with connection.transaction():
        if any(connection.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
               for table in ("members", "books", "loans")):
            return
        for member in (("S1", "Ali", "ACTIVE"), ("S2", "Sara", "ACTIVE")):
            connection.execute("INSERT INTO members VALUES (%s, %s, %s)", member)
        for book in ((1, "Clean Code", "Robert Martin"),
                     (2, "Refactoring", "Martin Fowler"),
                     (3, "Design Patterns", "Gamma et al.")):
            connection.execute("INSERT INTO books VALUES (%s, %s, %s, %s)",
                               (*book, INITIAL_BOOK_COPIES))


def get_member(connection, member_id):
    # Serialize borrowing by the same member so the three-loan limit holds.
    return connection.execute("SELECT * FROM members WHERE id = %s FOR UPDATE",
                              (member_id,)).fetchone()


def count_active_loans(connection, member_id):
    return connection.execute(
        "SELECT COUNT(*) AS count FROM loans WHERE member_id = %s AND returned = 0",
        (member_id,)).fetchone()["count"]


def get_book(connection, book_id):
    return connection.execute("SELECT * FROM books WHERE id = %s", (book_id,)).fetchone()


def decrease_availability(connection, book_id):
    return connection.execute(
        "UPDATE books SET available = available - 1 WHERE id = %s AND available > 0",
        (book_id,)).rowcount == 1


def create_loan(connection, member_id, book_id, borrowed_on, due_date):
    connection.execute("""
        INSERT INTO loans (member_id, book_id, borrow_date, due_date, returned)
        VALUES (%s, %s, %s, %s, 0)
    """, (member_id, book_id, borrowed_on, due_date))


def get_active_loan(connection, member_id, book_id):
    return connection.execute("""
        SELECT id, due_date FROM loans
        WHERE member_id = %s AND book_id = %s AND returned = 0
        ORDER BY id LIMIT 1 FOR UPDATE
    """, (member_id, book_id)).fetchone()


def close_loan(connection, loan_id):
    connection.execute("UPDATE loans SET returned = 1 WHERE id = %s", (loan_id,))


def increase_availability(connection, book_id):
    connection.execute("UPDATE books SET available = available + 1 WHERE id = %s",
                       (book_id,))


def find_books(connection, keyword):
    pattern = "%" + keyword + "%"
    return connection.execute("""
        SELECT id, title, author, available FROM books
        WHERE title ILIKE %s OR author ILIKE %s ORDER BY id
    """, (pattern, pattern)).fetchall()
