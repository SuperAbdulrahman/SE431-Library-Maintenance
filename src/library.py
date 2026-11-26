
"""Library business rules and command-line interface."""

from datetime import datetime, timedelta

from . import database
from .constraints import (
    MAX_ACTIVE_LOANS,
    LOAN_PERIOD_DAYS,
    FINE_PER_DAY,
)


def validate_member(connection, member_id):
    member = database.get_member(connection, member_id)

    if member is None:
        raise ValueError("Member not found")

    if member["status"] != "ACTIVE":
        raise ValueError("Member is not active")


def check_borrow_limit(connection, member_id):
    active_loans = database.count_active_loans(
        connection, member_id
    )

    if active_loans >= MAX_ACTIVE_LOANS:
        raise ValueError("Borrowing limit reached")


def check_book_availability(connection, book_id):
    book = database.get_book(connection, book_id)

    if book is None:
        raise ValueError("Book not found")

    if book["available"] <= 0:
        raise ValueError("Book is unavailable")


def calculate_due_date(borrow_date):
    return borrow_date + timedelta(
        days=LOAN_PERIOD_DAYS
    )


def calculate_fine(due_date, returned_on):
    overdue_days = max(
        0, (returned_on - due_date).days
    )

    return overdue_days * FINE_PER_DAY


def borrow(connection, member_id, book_id, borrowed_on=None):
    borrowed_on = borrowed_on or datetime.now()

    with connection.transaction():
        validate_member(connection, member_id)
        check_borrow_limit(connection, member_id)
        check_book_availability(connection, book_id)

        if not database.decrease_availability(
            connection, book_id
        ):
            raise ValueError("Book is unavailable")

        due_date = calculate_due_date(borrowed_on)

        database.create_loan(
            connection,
            member_id,
            book_id,
            borrowed_on,
            due_date,
        )

    return due_date


def return_book(connection, member_id, book_id, returned_on=None):
    returned_on = returned_on or datetime.now()

    with connection.transaction():
        loan = database.get_active_loan(
            connection, member_id, book_id
        )

        if loan is None:
            raise ValueError(
                "No active loan for this member and book"
            )

        due_date = loan["due_date"]
        fine = calculate_fine(due_date, returned_on)

        database.close_loan(connection, loan["id"])
        database.increase_availability(
            connection, book_id
        )

    return fine


def search_books(connection, keyword):
    return database.find_books(connection, keyword)


def main():
    connection = database.connect()

    try:
        database.setup(connection)

        database.seed_demo_data(connection)

        while True:
            print("\n1. Search  2. Borrow  3. Return  4. Exit")
            choice = input("Choose: ").strip()

            if choice == "4":
                break

            try:
                if choice == "1":
                    keyword = input("Title/author: ").strip()

                    for book in search_books(connection, keyword):
                        print(
                            f'{book["id"]}: {book["title"]} - '
                            f'{book["author"]} '
                            f'(Available: {bool(book["available"])})'
                        )

                elif choice in ("2", "3"):
                    member_id = input("Member ID: ").strip()
                    book_id = int(input("Book ID: "))

                    if choice == "2":
                        due = borrow(
                            connection, member_id, book_id
                        )
                        print(f"Borrowed. Due: {due.date()}")

                    else:
                        fine = return_book(
                            connection, member_id, book_id
                        )
                        print(f"Returned. Fine: {fine}")

                else:
                    print("Invalid option")

            except ValueError as error:
                print(error)

    finally:
        connection.close()


if __name__ == "__main__":
    main()
