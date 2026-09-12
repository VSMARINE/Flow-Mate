from flask import (
    Flask,
    render_template,
    request,
    redirect,
    session,
    flash,
    jsonify,
    send_from_directory
)

from database import create_tables, get_connection
from datetime import datetime
from zoneinfo import ZoneInfo
from werkzeug.utils import secure_filename

from pymongo.errors import DuplicateKeyError

import os
import calendar


app = Flask(__name__)

app.secret_key = os.getenv(
    "FLASK_SECRET_KEY",
    "student_productivity_secret"
)


# ==============================
# UPLOAD CONFIGURATION
# ==============================

UPLOAD_FOLDER = "uploads"

ALLOWED_EXTENSIONS = {
    "pdf",
    "doc",
    "docx",
    "ppt",
    "pptx",
    "txt"
}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in ALLOWED_EXTENSIONS
    )


# ==============================
# MONGODB HELPERS
# ==============================

def get_db():
    return get_connection()


def next_id(collection_name):
    """
    Keep integer IDs so the existing routes such as
    /edit_task/1 continue to work.
    """

    db = get_db()

    last_document = db[collection_name].find_one(
        {},
        sort=[("id", -1)]
    )

    if last_document is None:
        return 1

    return int(last_document.get("id", 0)) + 1


def india_now():
    return datetime.now(ZoneInfo("Asia/Kolkata"))


def document_to_dict(document):
    """
    Remove MongoDB's internal _id field so templates continue
    to work with the same field names as the old SQLite version.
    """

    if document is None:
        return None

    document = dict(document)
    document.pop("_id", None)

    return document


def documents_to_dicts(documents):
    return [
        document_to_dict(document)
        for document in documents
    ]


# ==============================
# INITIALIZE MONGODB
# ==============================

create_tables()


# ==============================
# HOME
# ==============================

@app.route("/")
def home():
    return render_template("index.html")


# ==============================
# LOGIN
# ==============================

@app.route("/login", methods=["GET", "POST"])
def login():

    if request.method == "POST":

        username = request.form["username"].strip()
        password = request.form["password"]

        db = get_db()

        user = db.users.find_one({
            "username": username,
            "password": password
        })

        if user:

            session["user_id"] = user["id"]
            session["username"] = user["username"]

            flash(
                "Login Successful!",
                "success"
            )

            return redirect("/dashboard")

        flash(
            "Invalid Username or Password",
            "danger"
        )

        return redirect("/login")

    return render_template("login.html")


# ==============================
# REGISTER
# ==============================

@app.route("/register", methods=["GET", "POST"])
def register():

    if request.method == "POST":

        fullname = request.form["fullname"].strip()
        email = request.form["email"].strip()
        username = request.form["username"].strip()
        password = request.form["password"]

        db = get_db()

        if db.users.find_one({
            "username": username
        }):

            flash(
                "Username already exists!",
                "danger"
            )

            return redirect("/register")

        if db.users.find_one({
            "email": email
        }):

            flash(
                "Email already registered!",
                "danger"
            )

            return redirect("/register")

        user_document = {
            "id": next_id("users"),
            "full_name": fullname,
            "email": email,
            "username": username,
            "password": password,
            "created_at": india_now().strftime(
                "%Y-%m-%d %H:%M:%S"
            )
        }

        try:

            db.users.insert_one(
                user_document
            )

        except DuplicateKeyError:

            flash(
                "Username or email already exists!",
                "danger"
            )

            return redirect("/register")

        flash(
            "Registration Successful. Please Login.",
            "success"
        )

        return redirect("/login")

    return render_template("register.html")


# ==============================
# DASHBOARD
# ==============================

@app.route("/dashboard")
def dashboard():

    if "user_id" not in session:

        flash(
            "Please login first.",
            "warning"
        )

        return redirect("/login")

    return render_template(
        "dashboard.html",
        username=session["username"]
    )


# ==============================
# TASKS
# ==============================

@app.route("/tasks")
def tasks():

    if "user_id" not in session:

        flash(
            "Please login first.",
            "warning"
        )

        return redirect("/login")

    db = get_db()

    all_tasks = documents_to_dicts(
        db.tasks.find({
            "user_id": session["user_id"]
        }).sort("id", -1)
    )

    return render_template(
        "tasks.html",
        tasks=all_tasks
    )


# ==============================
# ADD TASK
# ==============================

@app.route("/add_task", methods=["POST"])
def add_task():

    if "user_id" not in session:
        return redirect("/login")

    title = request.form["title"].strip()
    description = request.form["description"].strip()
    priority = request.form["priority"]
    due_date = request.form["due_date"]

    db = get_db()

    db.tasks.insert_one({
        "id": next_id("tasks"),
        "user_id": session["user_id"],
        "title": title,
        "description": description,
        "priority": priority,
        "due_date": due_date,
        "status": "Pending",
        "created_at": india_now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    })

    flash(
        "Task Added Successfully!",
        "success"
    )

    return redirect("/tasks")


# ==============================
# DELETE TASK
# ==============================

@app.route("/delete_task/<int:id>")
def delete_task(id):

    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    db.tasks.delete_one({
        "id": id,
        "user_id": session["user_id"]
    })

    flash(
        "Task Deleted!",
        "danger"
    )

    return redirect("/tasks")


# ==============================
# EDIT TASK
# ==============================

@app.route("/edit_task/<int:id>")
def edit_task(id):

    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    task = db.tasks.find_one({
        "id": id,
        "user_id": session["user_id"]
    })

    task = document_to_dict(task)

    if task is None:

        flash(
            "Task not found!",
            "danger"
        )

        return redirect("/tasks")

    return render_template(
        "edit_task.html",
        task=task
    )


# ==============================
# UPDATE TASK
# ==============================

@app.route("/update_task/<int:id>", methods=["POST"])
def update_task(id):

    if "user_id" not in session:
        return redirect("/login")

    title = request.form["title"].strip()
    description = request.form["description"].strip()
    priority = request.form["priority"]
    due_date = request.form["due_date"]
    status = request.form["status"]

    db = get_db()

    db.tasks.update_one(
        {
            "id": id,
            "user_id": session["user_id"]
        },
        {
            "$set": {
                "title": title,
                "description": description,
                "priority": priority,
                "due_date": due_date,
                "status": status
            }
        }
    )

    flash(
        "Task Updated Successfully!",
        "success"
    )

    return redirect("/tasks")


# ==========================================
# NOTES
# ==========================================

@app.route("/notes")
def notes():

    if "user_id" not in session:

        flash(
            "Please login first.",
            "warning"
        )

        return redirect("/login")

    db = get_db()

    all_notes = documents_to_dicts(
        db.notes.find({
            "user_id": session["user_id"]
        }).sort("id", -1)
    )

    return render_template(
        "notes.html",
        notes=all_notes
    )


# ==============================
# ADD NOTE + DOCUMENT
# ==============================

@app.route("/add_note", methods=["POST"])
def add_note():

    if "user_id" not in session:
        return redirect("/login")

    title = request.form["title"].strip()
    content = request.form["content"].strip()

    document_name = None
    document_path = None

    file = request.files.get("document")

    if file and file.filename != "":

        if not allowed_file(file.filename):

            flash(
                "Invalid file type! Please upload PDF, DOC, DOCX, PPT, PPTX or TXT.",
                "danger"
            )

            return redirect("/notes")

        document_name = secure_filename(
            file.filename
        )

        timestamp = india_now().strftime(
            "%Y%m%d%H%M%S%f"
        )

        unique_filename = (
            f"{session['user_id']}_"
            f"{timestamp}_"
            f"{document_name}"
        )

        document_path = unique_filename

        file.save(
            os.path.join(
                app.config["UPLOAD_FOLDER"],
                unique_filename
            )
        )

    db = get_db()

    db.notes.insert_one({
        "id": next_id("notes"),
        "user_id": session["user_id"],
        "title": title,
        "content": content,
        "document_name": document_name,
        "document_path": document_path,
        "created_at": india_now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    })

    flash(
        "Note Saved Successfully!",
        "success"
    )

    return redirect("/notes")


# ==============================
# VIEW DOCUMENT
# ==============================

@app.route("/document/<int:id>")
def view_document(id):

    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    note = db.notes.find_one({
        "id": id,
        "user_id": session["user_id"]
    })

    note = document_to_dict(note)

    if note is None or not note.get(
        "document_path"
    ):

        flash(
            "Document not found!",
            "danger"
        )

        return redirect("/notes")

    return send_from_directory(
        app.config["UPLOAD_FOLDER"],
        note["document_path"],
        as_attachment=False
    )


# ==============================
# DOWNLOAD DOCUMENT
# ==============================

@app.route("/download_document/<int:id>")
def download_document(id):

    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    note = db.notes.find_one({
        "id": id,
        "user_id": session["user_id"]
    })

    note = document_to_dict(note)

    if note is None or not note.get(
        "document_path"
    ):

        flash(
            "Document not found!",
            "danger"
        )

        return redirect("/notes")

    return send_from_directory(
        app.config["UPLOAD_FOLDER"],
        note["document_path"],
        as_attachment=True,
        download_name=note.get(
            "document_name",
            "document"
        )
    )


# ==============================
# DELETE NOTE
# ==============================

@app.route("/delete_note/<int:id>")
def delete_note(id):

    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    note = db.notes.find_one({
        "id": id,
        "user_id": session["user_id"]
    })

    note = document_to_dict(note)

    if note is None:

        flash(
            "Note not found!",
            "danger"
        )

        return redirect("/notes")

    db.notes.delete_one({
        "id": id,
        "user_id": session["user_id"]
    })

    document_path = note.get(
        "document_path"
    )

    if document_path:

        file_path = os.path.join(
            app.config["UPLOAD_FOLDER"],
            document_path
        )

        if os.path.exists(file_path):
            os.remove(file_path)

    flash(
        "Note Deleted Successfully!",
        "danger"
    )

    return redirect("/notes")


# ==============================
# EDIT NOTE
# ==============================

@app.route("/edit_note/<int:id>")
def edit_note(id):

    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    note = db.notes.find_one({
        "id": id,
        "user_id": session["user_id"]
    })

    note = document_to_dict(note)

    if note is None:

        flash(
            "Note not found!",
            "danger"
        )

        return redirect("/notes")

    return render_template(
        "edit_note.html",
        note=note
    )


# ==========================================
# UPDATE NOTE + REPLACE DOCUMENT
# ==========================================

@app.route(
    "/update_note/<int:id>",
    methods=["POST"]
)
def update_note(id):

    if "user_id" not in session:
        return redirect("/login")

    title = request.form["title"].strip()
    content = request.form["content"].strip()

    db = get_db()

    existing_note = db.notes.find_one({
        "id": id,
        "user_id": session["user_id"]
    })

    existing_note = document_to_dict(
        existing_note
    )

    if existing_note is None:

        flash(
            "Note not found!",
            "danger"
        )

        return redirect("/notes")

    document_name = existing_note.get(
        "document_name"
    )

    document_path = existing_note.get(
        "document_path"
    )

    file = request.files.get("document")

    if file and file.filename != "":

        if not allowed_file(file.filename):

            flash(
                "Invalid file type! Please upload PDF, DOC, DOCX, PPT, PPTX or TXT.",
                "danger"
            )

            return redirect(
                f"/edit_note/{id}"
            )

        if document_path:

            old_file_path = os.path.join(
                app.config["UPLOAD_FOLDER"],
                document_path
            )

            if os.path.exists(old_file_path):
                os.remove(old_file_path)

        document_name = secure_filename(
            file.filename
        )

        timestamp = india_now().strftime(
            "%Y%m%d%H%M%S%f"
        )

        unique_filename = (
            f"{session['user_id']}_"
            f"{timestamp}_"
            f"{document_name}"
        )

        document_path = unique_filename

        file.save(
            os.path.join(
                app.config["UPLOAD_FOLDER"],
                unique_filename
            )
        )

    db.notes.update_one(
        {
            "id": id,
            "user_id": session["user_id"]
        },
        {
            "$set": {
                "title": title,
                "content": content,
                "document_name": document_name,
                "document_path": document_path
            }
        }
    )

    flash(
        "Note Updated Successfully!",
        "success"
    )

    return redirect("/notes")


# ==========================================
# EXPENSES
# ==========================================

@app.route("/expenses")
def expenses():

    if "user_id" not in session:

        flash(
            "Please login first.",
            "warning"
        )

        return redirect("/login")

    user_id = session["user_id"]

    india_time = india_now()

    current_month = india_time.strftime(
        "%Y-%m"
    )

    today = india_time.date()

    db = get_db()

    # ------------------------------
    # GET ALL EXPENSES
    # ------------------------------

    all_expenses = documents_to_dicts(
        db.expenses.find({
            "user_id": user_id
        })
    )

    all_expenses.sort(
        key=lambda item: (
            item.get("expense_date", ""),
            item.get("payment_time", ""),
            item.get("id", 0)
        ),
        reverse=True
    )

    # ------------------------------
    # CURRENT MONTH TOTAL SPENDING
    # ------------------------------

    monthly_expense = 0

    for expense in all_expenses:

        if str(
            expense.get(
                "expense_date",
                ""
            )
        ).startswith(current_month):

            monthly_expense += float(
                expense.get("amount", 0)
            )

    # ------------------------------
    # GET MONTHLY INCOME
    # ------------------------------

    income_record = db.monthly_income.find_one({
        "user_id": user_id,
        "month": current_month
    })

    income_record = document_to_dict(
        income_record
    )

    if income_record:

        monthly_income = float(
            income_record.get(
                "income_amount",
                0
            )
        )

        income_source = income_record.get(
            "income_source",
            "Income"
        )

    else:

        monthly_income = 0
        income_source = "Not Set"

    # ------------------------------
    # REMAINING BALANCE
    # ------------------------------

    remaining_balance = (
        monthly_income - monthly_expense
    )

    # ------------------------------
    # DAYS REMAINING IN MONTH
    # ------------------------------

    last_day = calendar.monthrange(
        today.year,
        today.month
    )[1]

    days_remaining = (
        last_day - today.day + 1
    )

    # ------------------------------
    # SAFE DAILY SPENDING
    # ------------------------------

    if (
        remaining_balance > 0
        and days_remaining > 0
    ):

        safe_daily_spending = (
            remaining_balance
            / days_remaining
        )

    else:

        safe_daily_spending = 0

    # ------------------------------
    # CATEGORY-WISE MONTHLY SPENDING
    # ------------------------------

    category_spending_map = {}

    for expense in all_expenses:

        expense_date = str(
            expense.get(
                "expense_date",
                ""
            )
        )

        if not expense_date.startswith(
            current_month
        ):
            continue

        category = expense.get(
            "category",
            "Other"
        )

        amount = float(
            expense.get("amount", 0)
        )

        category_spending_map[
            category
        ] = (
            category_spending_map.get(
                category,
                0
            ) + amount
        )

    category_spending = [
        {
            "category": category,
            "total_spent": spent
        }
        for category, spent
        in sorted(
            category_spending_map.items(),
            key=lambda item: item[1],
            reverse=True
        )
    ]

    # ------------------------------
    # GET CATEGORY BUDGETS
    # ------------------------------

    budget_records = documents_to_dicts(
        db.category_budgets.find({
            "user_id": user_id,
            "month": current_month
        })
    )

    category_budgets = {}

    for budget in budget_records:

        category_budgets[
            budget["category"]
        ] = float(
            budget.get(
                "budget_amount",
                0
            )
        )

    # ------------------------------
    # COMBINE CATEGORY DATA
    # ------------------------------

    category_summary = []

    all_categories = {
        "Food",
        "Travel",
        "Education",
        "Shopping",
        "Entertainment",
        "Health",
        "Other"
    }

    for item in category_spending:
        all_categories.add(
            item["category"]
        )

    for category in sorted(
        all_categories
    ):

        spent = category_spending_map.get(
            category,
            0
        )

        budget = category_budgets.get(
            category,
            0
        )

        category_remaining = (
            budget - spent
        )

        if (
            category_remaining > 0
            and days_remaining > 0
        ):

            daily_limit = (
                category_remaining
                / days_remaining
            )

        else:

            daily_limit = 0

        if budget <= 0:

            status = "Not Set"
            status_class = "secondary"

        elif spent > budget:

            status = "Over Budget"
            status_class = "danger"

        elif spent >= budget * 0.80:

            status = "Almost Used"
            status_class = "warning"

        else:

            status = "Safe"
            status_class = "success"

        category_summary.append({
            "category": category,
            "budget": budget,
            "spent": spent,
            "remaining": category_remaining,
            "daily_limit": daily_limit,
            "status": status,
            "status_class": status_class
        })

    # ------------------------------
    # TOTAL ALL-TIME EXPENSE
    # ------------------------------

    total_expense = sum(
        float(
            expense.get("amount", 0)
        )
        for expense in all_expenses
    )

    return render_template(
        "expenses.html",
        expenses=all_expenses,
        total_expense=total_expense,
        current_month=current_month,
        monthly_income=monthly_income,
        income_source=income_source,
        monthly_expense=monthly_expense,
        remaining_balance=remaining_balance,
        days_remaining=days_remaining,
        safe_daily_spending=safe_daily_spending,
        category_summary=category_summary
    )


# ==============================
# SET MONTHLY INCOME
# ==============================

@app.route(
    "/set_monthly_income",
    methods=["POST"]
)
def set_monthly_income():

    if "user_id" not in session:
        return redirect("/login")

    month = request.form["month"]

    income_amount = request.form[
        "income_amount"
    ]

    income_source = request.form[
        "income_source"
    ].strip()

    try:

        income_amount = float(
            income_amount
        )

    except ValueError:

        flash(
            "Please enter a valid income amount!",
            "danger"
        )

        return redirect("/expenses")

    if income_amount < 0:

        flash(
            "Income amount cannot be negative!",
            "danger"
        )

        return redirect("/expenses")

    if not income_source:
        income_source = "Income"

    db = get_db()

    db.monthly_income.update_one(
        {
            "user_id": session["user_id"],
            "month": month
        },
        {
            "$set": {
                "income_amount": income_amount,
                "income_source": income_source
            },
            "$setOnInsert": {
                "id": next_id(
                    "monthly_income"
                ),
                "created_at": india_now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            }
        },
        upsert=True
    )

    flash(
        "Monthly income updated successfully!",
        "success"
    )

    return redirect("/expenses")


# ==============================
# SET CATEGORY BUDGET
# ==============================

@app.route(
    "/set_category_budget",
    methods=["POST"]
)
def set_category_budget():

    if "user_id" not in session:
        return redirect("/login")

    month = request.form["month"]
    category = request.form["category"]

    budget_amount = request.form[
        "budget_amount"
    ]

    try:

        budget_amount = float(
            budget_amount
        )

    except ValueError:

        flash(
            "Please enter a valid budget amount!",
            "danger"
        )

        return redirect("/expenses")

    if budget_amount < 0:

        flash(
            "Budget cannot be negative!",
            "danger"
        )

        return redirect("/expenses")

    db = get_db()

    db.category_budgets.update_one(
        {
            "user_id": session["user_id"],
            "month": month,
            "category": category
        },
        {
            "$set": {
                "budget_amount": budget_amount
            },
            "$setOnInsert": {
                "id": next_id(
                    "category_budgets"
                ),
                "created_at": india_now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
            }
        },
        upsert=True
    )

    flash(
        "Category budget updated successfully!",
        "success"
    )

    return redirect("/expenses")


# ==============================
# ADD EXPENSE
# ==============================

@app.route(
    "/add_expense",
    methods=["POST"]
)
def add_expense():

    if "user_id" not in session:
        return redirect("/login")

    amount = request.form["amount"]
    category = request.form["category"]

    description = request.form[
        "description"
    ].strip()

    expense_date = request.form[
        "expense_date"
    ]

    payment_method = request.form.get(
        "payment_method",
        ""
    ).strip()

    payment_time = request.form.get(
        "payment_time",
        ""
    ).strip()

    paid_to = request.form.get(
        "paid_to",
        ""
    ).strip()

    upi_id = request.form.get(
        "upi_id",
        ""
    ).strip()

    payment_location = request.form.get(
        "payment_location",
        ""
    ).strip()

    try:

        amount = float(amount)

    except ValueError:

        flash(
            "Please enter a valid amount!",
            "danger"
        )

        return redirect("/expenses")

    if amount <= 0:

        flash(
            "Amount must be greater than zero!",
            "danger"
        )

        return redirect("/expenses")

    db = get_db()

    db.expenses.insert_one({
        "id": next_id("expenses"),
        "user_id": session["user_id"],
        "amount": amount,
        "category": category,
        "description": description,
        "expense_date": expense_date,
        "payment_method": payment_method,
        "payment_time": payment_time,
        "paid_to": paid_to,
        "upi_id": upi_id,
        "payment_location": payment_location,
        "created_at": india_now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    })

    flash(
        "Expense Added Successfully!",
        "success"
    )

    return redirect("/expenses")


# ==========================================
# VIEW EXPENSE DETAILS
# ==========================================

@app.route("/view_expense/<int:id>")
def view_expense(id):

    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    expense = db.expenses.find_one({
        "id": id,
        "user_id": session["user_id"]
    })

    expense = document_to_dict(
        expense
    )

    if expense is None:

        flash(
            "Expense not found!",
            "danger"
        )

        return redirect("/expenses")

    return render_template(
        "view_expense.html",
        expense=expense
    )


# ==============================
# DELETE EXPENSE
# ==============================

@app.route("/delete_expense/<int:id>")
def delete_expense(id):

    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    db.expenses.delete_one({
        "id": id,
        "user_id": session["user_id"]
    })

    flash(
        "Expense Deleted Successfully!",
        "danger"
    )

    return redirect("/expenses")


# ==============================
# EDIT EXPENSE
# ==============================

@app.route("/edit_expense/<int:id>")
def edit_expense(id):

    if "user_id" not in session:
        return redirect("/login")

    db = get_db()

    expense = db.expenses.find_one({
        "id": id,
        "user_id": session["user_id"]
    })

    expense = document_to_dict(
        expense
    )

    if expense is None:

        flash(
            "Expense not found!",
            "danger"
        )

        return redirect("/expenses")

    return render_template(
        "edit_expense.html",
        expense=expense
    )


# ==============================
# UPDATE EXPENSE
# ==============================

@app.route(
    "/update_expense/<int:id>",
    methods=["POST"]
)
def update_expense(id):

    if "user_id" not in session:
        return redirect("/login")

    amount = request.form["amount"]
    category = request.form["category"]

    description = request.form[
        "description"
    ].strip()

    expense_date = request.form[
        "expense_date"
    ]

    payment_method = request.form.get(
        "payment_method",
        ""
    ).strip()

    payment_time = request.form.get(
        "payment_time",
        ""
    ).strip()

    paid_to = request.form.get(
        "paid_to",
        ""
    ).strip()

    upi_id = request.form.get(
        "upi_id",
        ""
    ).strip()

    payment_location = request.form.get(
        "payment_location",
        ""
    ).strip()

    try:

        amount = float(amount)

    except ValueError:

        flash(
            "Please enter a valid amount!",
            "danger"
        )

        return redirect(
            f"/edit_expense/{id}"
        )

    if amount <= 0:

        flash(
            "Amount must be greater than zero!",
            "danger"
        )

        return redirect(
            f"/edit_expense/{id}"
        )

    db = get_db()

    db.expenses.update_one(
        {
            "id": id,
            "user_id": session["user_id"]
        },
        {
            "$set": {
                "amount": amount,
                "category": category,
                "description": description,
                "expense_date": expense_date,
                "payment_method": payment_method,
                "payment_time": payment_time,
                "paid_to": paid_to,
                "upi_id": upi_id,
                "payment_location": payment_location
            }
        }
    )

    flash(
        "Expense Updated Successfully!",
        "success"
    )

    return redirect("/expenses")


# ==============================
# POMODORO
# ==============================

@app.route("/pomodoro")
def pomodoro():

    if "user_id" not in session:

        flash(
            "Please login first.",
            "warning"
        )

        return redirect("/login")

    return render_template(
        "pomodoro.html"
    )


# ==============================
# SAVE POMODORO SESSION
# ==============================

@app.route(
    "/save_pomodoro_session",
    methods=["POST"]
)
def save_pomodoro_session():

    if "user_id" not in session:

        return jsonify({
            "success": False,
            "message": "User not logged in"
        }), 401

    data = request.get_json()

    if not data:

        return jsonify({
            "success": False,
            "message": "No data received"
        }), 400

    focus_minutes = data.get(
        "focus_minutes"
    )

    if not focus_minutes:

        return jsonify({
            "success": False,
            "message": "Invalid focus time"
        }), 400

    try:

        focus_minutes = int(
            focus_minutes
        )

    except (TypeError, ValueError):

        return jsonify({
            "success": False,
            "message": "Invalid focus time"
        }), 400

    if focus_minutes <= 0:

        return jsonify({
            "success": False,
            "message": "Invalid focus time"
        }), 400

    completed_at = india_now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )

    db = get_db()

    db.pomodoro_sessions.insert_one({
        "id": next_id(
            "pomodoro_sessions"
        ),
        "user_id": session["user_id"],
        "focus_minutes": focus_minutes,
        "completed_at": completed_at
    })

    return jsonify({
        "success": True,
        "message": "Pomodoro session saved successfully"
    })


# ==============================
# POMODORO DATA
# ==============================

@app.route("/pomodoro_data")
def pomodoro_data():

    if "user_id" not in session:

        return jsonify({
            "success": False,
            "message": "User not logged in"
        }), 401

    india_today = india_now().strftime(
        "%Y-%m-%d"
    )

    db = get_db()

    today_sessions = list(
        db.pomodoro_sessions.find({
            "user_id": session["user_id"],
            "completed_at": {
                "$regex": f"^{india_today}"
            }
        })
    )

    sessions_count = len(
        today_sessions
    )

    focus_minutes_today = sum(
        int(
            item.get(
                "focus_minutes",
                0
            )
        )
        for item in today_sessions
    )

    recent_sessions = documents_to_dicts(
        db.pomodoro_sessions.find({
            "user_id": session["user_id"]
        })
        .sort("completed_at", -1)
        .limit(5)
    )

    sessions = []

    for item in recent_sessions:

        sessions.append({
            "focus_minutes": item[
                "focus_minutes"
            ],
            "completed_at": item[
                "completed_at"
            ]
        })

    return jsonify({

        "success": True,

        "sessions":
            sessions_count,

        "focus_minutes":
            focus_minutes_today,

        "recent_sessions":
            sessions

    })


# ==============================
# RUN APP
# ==============================

if __name__ == "__main__":

    app.run(
        host="127.0.0.1",
        port=5000,
        debug=True
    )
