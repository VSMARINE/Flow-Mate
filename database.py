import os

from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import PyMongoError


# ==============================
# DATABASE CONFIGURATION
# ==============================

# Put your MongoDB Atlas connection string in the MONGO_URI
# environment variable.
#
# Example:
# MONGO_URI=mongodb+srv://username:password@cluster.mongodb.net/
#
MONGO_URI = os.getenv("MONGO_URI")

# Database name for the Smart Student Manager.
DB_NAME = os.getenv("MONGO_DB_NAME", "smart_student_manager")


# ==============================
# MONGODB CONNECTION
# ==============================

_client = None
_db = None


def get_connection():
    """
    Connect to MongoDB Atlas and return the database object.

    The function name is kept as get_connection() so app.py can
    continue to have one clear database entry point while it is
    being migrated from SQLite to MongoDB.
    """

    global _client, _db

    if not MONGO_URI:
        raise RuntimeError(
            "MONGO_URI environment variable is not set. "
            "Add your MongoDB Atlas connection string before running the app."
        )

    if _db is None:
        _client = MongoClient(
            MONGO_URI,
            serverSelectionTimeoutMS=10000
        )

        # Check that MongoDB is reachable.
        _client.admin.command("ping")

        _db = _client[DB_NAME]

    return _db


# ==============================
# CREATE COLLECTIONS / INDEXES
# ==============================

def create_tables():
    """
    MongoDB does not use SQL CREATE TABLE statements.

    Collections are created automatically when documents are inserted.
    We create them here explicitly and add indexes corresponding to
    important SQLite constraints and query patterns.
    """

    db = get_connection()

    collection_names = [
        "users",
        "tasks",
        "notes",
        "expenses",
        "monthly_income",
        "category_budgets",
        "pomodoro_sessions",
    ]

    existing_collections = db.list_collection_names()

    for collection_name in collection_names:
        if collection_name not in existing_collections:
            db.create_collection(collection_name)

    # ==========================
    # USERS
    # ==========================

    # Replaces UNIQUE(email) and UNIQUE(username).
    db.users.create_index(
        [("email", ASCENDING)],
        unique=True,
        name="unique_email"
    )

    db.users.create_index(
        [("username", ASCENDING)],
        unique=True,
        name="unique_username"
    )

    # ==========================
    # TASKS
    # ==========================

    db.tasks.create_index(
        [("user_id", ASCENDING)],
        name="tasks_user_id"
    )

    db.tasks.create_index(
        [("user_id", ASCENDING), ("due_date", ASCENDING)],
        name="tasks_user_due_date"
    )

    # ==========================
    # NOTES
    # ==========================

    db.notes.create_index(
        [("user_id", ASCENDING)],
        name="notes_user_id"
    )

    # ==========================
    # EXPENSES
    # ==========================

    db.expenses.create_index(
        [("user_id", ASCENDING)],
        name="expenses_user_id"
    )

    db.expenses.create_index(
        [("user_id", ASCENDING), ("expense_date", DESCENDING)],
        name="expenses_user_date"
    )

    db.expenses.create_index(
        [("user_id", ASCENDING), ("category", ASCENDING)],
        name="expenses_user_category"
    )

    # ==========================
    # MONTHLY INCOME
    # ==========================

    # Replaces UNIQUE(user_id, month).
    db.monthly_income.create_index(
        [("user_id", ASCENDING), ("month", ASCENDING)],
        unique=True,
        name="unique_user_month_income"
    )

    # ==========================
    # CATEGORY BUDGETS
    # ==========================

    # Replaces UNIQUE(user_id, month, category).
    db.category_budgets.create_index(
        [
            ("user_id", ASCENDING),
            ("month", ASCENDING),
            ("category", ASCENDING)
        ],
        unique=True,
        name="unique_user_month_category_budget"
    )

    # ==========================
    # POMODORO
    # ==========================

    db.pomodoro_sessions.create_index(
        [("user_id", ASCENDING)],
        name="pomodoro_user_id"
    )

    db.pomodoro_sessions.create_index(
        [("user_id", ASCENDING), ("completed_at", DESCENDING)],
        name="pomodoro_user_completed"
    )

    return db


# ==============================
# DATABASE HEALTH CHECK
# ==============================

def test_connection():
    """
    Return True if MongoDB Atlas is reachable.
    """

    try:
        db = get_connection()
        db.command("ping")
        return True

    except PyMongoError as error:
        print("MongoDB connection error:", error)
        return False


# ==============================
# CLOSE DATABASE CONNECTION
# ==============================

def close_connection():
    """
    Close the MongoDB client when the application is shutting down.
    """

    global _client, _db

    if _client is not None:
        _client.close()

    _client = None
    _db = None


# ==============================
# RUN DIRECTLY
# ==============================

if __name__ == "__main__":

    try:
        database = create_tables()

        print("MongoDB connected successfully!")
        print("Database:", database.name)
        print(
            "Collections:",
            ", ".join(database.list_collection_names())
        )

    except Exception as error:
        print("MongoDB setup failed:")
        print(error)

    finally:
        close_connection()
