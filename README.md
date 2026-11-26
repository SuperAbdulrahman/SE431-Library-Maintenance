# SE431 Library Maintenance

Simple Python CLI implementing the maintenance and refactoring described in
`SE431-Project-1-Revised.docx`:

- **MR-01:** new sample books start with 20 copies. Borrowing subtracts one;
  returning adds one. Search still displays Boolean availability.
- **MR-02:** PostgreSQL replaces SQLite, with SQL in `src/database.py` and a
  backup-and-migration command for existing SQLite records.
- Business rules remain: active members only, at most three active loans,
  a 14-day loan period, and one fine unit per full overdue day.

`src/library.py` contains the business helpers and CLI. `src/constraints.py`
centralizes the constants (the Python equivalent of the report's `Constraints.js`).
Database settings come from `DATABASE_URL`. Loan timestamps retain time of day
so fine calculation matches the legacy full-day rule.

## Run

With an existing PostgreSQL database and Python 3.10 or newer:

```sh
python -m pip install -r requirements.txt
export DATABASE_URL='postgresql://USER:PASSWORD@localhost/library'
python -m src.library
```

Tables are created on startup. An empty database receives the original sample
members S1/S2 and three sample titles with 20 available copies each. Existing
data is not reseeded or reset.

## Migrate existing SQLite records

Run this **before launching the CLI**, using an empty PostgreSQL destination:

```sh
python -m src.migrate /path/to/library.db
```

The command creates `library.db.backup` beside the source using SQLite's backup
API, preserves member/book/loan IDs and history, validates copied records and
foreign-key relationships, and resets the loan ID sequence. All destination
writes form one transaction; a failure rolls them back. It refuses a nonempty
destination or an existing backup file. The original SQLite file is read only.

Existing availability values are preserved exactly; 20 is only the default for
new sample books. Migration does not invent extra stock for existing titles.

## Tests

```sh
python -m unittest discover -s tests -v
```

Business-rule unit tests run without a database. For the report's TC01–TC10,
quantity changes, rollback and migration integration tests, set a separate test
database whose user can create schemas:

```sh
export TEST_DATABASE_URL='postgresql://USER:PASSWORD@localhost/library_test'
python -m unittest discover -s tests -v
```

Integration tests create and remove their own uniquely named schemas. Without
`TEST_DATABASE_URL`, they are explicitly skipped. No before/after metrics or
legacy execution results are claimed; `legacy/original.txt` is retained as given.
