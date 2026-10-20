import os
from dotenv import load_dotenv

load_dotenv()

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    session,
    flash,
    jsonify,
    send_from_directory,
    url_for
)

from database import create_tables, get_connection
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from werkzeug.utils import secure_filename

from pymongo.errors import DuplicateKeyError, PyMongoError
from authlib.integrations.flask_client import OAuth

import calendar
import secrets
import hmac
from functools import wraps
from flask import abort
from werkzeug.exceptions import RequestEntityTooLarge


app = Flask(__name__)

app.secret_key = os.getenv("FLASK_SECRET_KEY") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024  # Request cap; individual files have a stricter 500 KB limit.
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
if os.getenv("RENDER"):
    app.config["SESSION_COOKIE_SECURE"] = True


# ==============================
# GOOGLE OAUTH CONFIGURATION
# ==============================

oauth = OAuth(app)

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET")

google = None

if GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET:
    google = oauth.register(
        name="google",
        client_id=GOOGLE_CLIENT_ID,
        client_secret=GOOGLE_CLIENT_SECRET,
        server_metadata_url=(
            "https://accounts.google.com/.well-known/"
            "openid-configuration"
        ),
        client_kwargs={
            "scope": "openid email profile"
        }
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

MAX_FILE_SIZE = 500 * 1024          # 500 KB per file
MAX_USER_STORAGE = 5 * 1024 * 1024  # 5 MB per user account
MONGODB_WARNING_BYTES = 450 * 1024 * 1024  # Estimated database usage warning threshold
MONGODB_UPLOAD_STOP_BYTES = 500 * 1024 * 1024  # Stop uploads before the 512 MB plan limit
ADMIN_EMAILS = {
    item.strip().lower()
    for item in os.getenv("ADMIN_EMAILS", "").split(",")
    if item.strip()
}

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)


def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower()
        in ALLOWED_EXTENSIONS
    )


def get_upload_size(file):
    """Return the uploaded file size in bytes without saving it."""
    current_position = file.stream.tell()

    file.stream.seek(0, os.SEEK_END)
    size = file.stream.tell()

    file.stream.seek(current_position)
    return size


def get_user_storage_bytes(db, user_id, exclude_note_id=None):
    """
    Calculate the user's current document storage from the actual files.
    Older notes without document_size are supported automatically.
    """
    query = {
        "user_id": user_id,
        "document_path": {
            "$exists": True,
            "$nin": [None, ""]
        }
    }

    if exclude_note_id is not None:
        query["id"] = {"$ne": exclude_note_id}

    total = 0

    for note in db.notes.find(
        query,
        {"_id": 0, "document_path": 1, "document_size": 1}
    ):
        stored_size = note.get("document_size")

        if stored_size is not None:
            try:
                total += int(stored_size)
                continue
            except (TypeError, ValueError):
                pass

        file_path = os.path.join(
            app.config["UPLOAD_FOLDER"],
            note.get("document_path", "")
        )

        if os.path.isfile(file_path):
            total += os.path.getsize(file_path)

    return total


def get_database_storage_stats(db):
    """Return an estimate from MongoDB dbStats; Atlas usage is authoritative in Atlas metrics."""
    stats = db.command("dbStats", scale=1)
    data_bytes = int(stats.get("dataSize", 0) or 0)
    storage_bytes = int(stats.get("storageSize", 0) or 0)
    index_bytes = int(stats.get("indexSize", 0) or 0)
    # storageSize + indexSize is a useful operational estimate, not an exact Atlas billing/quota meter.
    estimated_bytes = storage_bytes + index_bytes
    if estimated_bytes <= 0:
        estimated_bytes = data_bytes + index_bytes
    if estimated_bytes < data_bytes:
        estimated_bytes = data_bytes + index_bytes
    if estimated_bytes < 0:
        estimated_bytes = 0
    return {
        "data_bytes": data_bytes,
        "storage_bytes": storage_bytes,
        "index_bytes": index_bytes,
        "estimated_bytes": estimated_bytes,
        "estimated_mb": estimated_bytes / (1024 * 1024),
        "warning": estimated_bytes >= MONGODB_WARNING_BYTES,
        "uploads_restricted": estimated_bytes >= MONGODB_UPLOAD_STOP_BYTES,
        "status": (
            "restricted" if estimated_bytes >= MONGODB_UPLOAD_STOP_BYTES
            else "warning" if estimated_bytes >= MONGODB_WARNING_BYTES
            else "normal"
        ),
    }


def user_is_admin():
    email = str(session.get("email", "")).strip().lower()
    return bool(email and email in ADMIN_EMAILS)


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        if not user_is_admin():
            abort(403)
        return view_func(*args, **kwargs)
    return wrapped


def format_bytes(num_bytes):
    value = float(max(0, int(num_bytes or 0)))
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{value:.1f} GB"


def validate_uploaded_file(file, db, user_id, exclude_note_id=None):
    """
    Validate extension, 500 KB individual limit and 5 MB per-user limit.
    Returns (True, file_size, "") when valid.
    """
    if not file or not file.filename:
        return True, 0, ""

    if not allowed_file(file.filename):
        return (
            False,
            0,
            "Invalid file type! Please upload PDF, DOC, DOCX, PPT, PPTX or TXT."
        )

    file_size = get_upload_size(file)

    if file_size > MAX_FILE_SIZE:
        return (
            False,
            file_size,
            "File size cannot exceed 500 KB."
        )

    try:
        global_stats = get_database_storage_stats(db)
    except Exception:
        app.logger.exception("Could not verify MongoDB storage before upload")
        return (
            False,
            file_size,
            "Storage capacity could not be verified right now. Please try again later."
        )

    if global_stats["uploads_restricted"]:
        return (
            False,
            file_size,
            "New uploads are temporarily disabled because the service storage is nearly full. Please try again later."
        )

    current_storage = get_user_storage_bytes(
        db,
        user_id,
        exclude_note_id=exclude_note_id
    )

    if current_storage + file_size > MAX_USER_STORAGE:
        remaining = max(
            MAX_USER_STORAGE - current_storage,
            0
        )

        if remaining == 0:
            message = (
                "Your 5 MB storage limit has been reached. "
                "Delete an existing document before uploading another file."
            )
        else:
            message = (
                "This file cannot be uploaded because it exceeds "
                f"your remaining storage of {remaining / 1024:.0f} KB."
            )

        return False, file_size, message

    return True, file_size, ""


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


@app.context_processor
def inject_template_flags():
    return {"is_admin": user_is_admin()}


@app.errorhandler(PyMongoError)
def handle_database_error(error):
    app.logger.exception("Database operation failed")
    return render_template(
        "error.html",
        error_title="Service temporarily unavailable",
        error_message="The database could not complete this request. Please try again in a moment. If the issue continues, contact the site administrator."
    ), 503


@app.errorhandler(403)
def handle_forbidden(error):
    return render_template(
        "error.html",
        error_title="Access denied",
        error_message="You do not have permission to access this page."
    ), 403


@app.errorhandler(500)
def handle_internal_error(error):
    app.logger.error("Unhandled server error: %s", error)
    return render_template(
        "error.html",
        error_title="Something went wrong",
        error_message="The request could not be completed. Your existing data has not intentionally been deleted. Please try again."
    ), 500


# ==============================
# HOME
# ==============================

@app.route("/")
def home():
    return render_template("index.html")


# ==============================
# GOOGLE LOGIN / REGISTER
# ==============================

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        flash(
            "Please use Continue with Google. Flow Mate only accepts verified Gmail accounts.",
            "warning"
        )
        return redirect("/login")

    return render_template("login.html")


@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        flash(
            "Please use Continue with Google. Flow Mate only accepts verified Gmail accounts.",
            "warning"
        )
        return redirect("/register")

    return render_template("register.html")


@app.route("/google/login")
def google_login():
    if google is None:
        flash(
            "Google login is not configured yet. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.",
            "danger"
        )
        return redirect("/login")

    session["google_auth_mode"] = "login"

    redirect_uri = url_for(
        "google_callback",
        _external=True
    )

    return google.authorize_redirect(
        redirect_uri,
        prompt="select_account"
    )


@app.route("/google/register")
def google_register():
    if google is None:
        flash(
            "Google registration is not configured yet. Add GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.",
            "danger"
        )
        return redirect("/register")

    session["google_auth_mode"] = "register"

    redirect_uri = url_for(
        "google_callback",
        _external=True
    )

    return google.authorize_redirect(
        redirect_uri,
        prompt="select_account"
    )


@app.route("/auth/google/callback")
def google_callback():
    if google is None:
        flash(
            "Google login is not configured yet.",
            "danger"
        )
        return redirect("/login")

    try:
        token = google.authorize_access_token()
        user_info = token.get("userinfo")

        if not user_info:
            user_info = google.userinfo()

    except Exception:
        flash(
            "Google authentication failed. Please try again.",
            "danger"
        )
        return redirect("/login")

    auth_mode = session.get("google_auth_mode", "login")

    email = str(
        user_info.get("email", "")
    ).strip().lower()

    email_verified = user_info.get(
        "email_verified",
        False
    )

    # Only real, Google-verified Gmail addresses are accepted.
    if (
        not email
        or not email.endswith("@gmail.com")
        or email_verified is not True
    ):
        flash(
            "Only verified Google Gmail accounts (@gmail.com) can use Flow Mate.",
            "danger"
        )
        return redirect("/login")

    google_sub = str(
        user_info.get("sub", "")
    ).strip()

    if not google_sub:
        flash(
            "Google account verification failed. Please try again.",
            "danger"
        )
        return redirect("/login")

    full_name = str(
        user_info.get("name")
        or email.split("@")[0]
    ).strip()

    db = get_db()

    # Existing account: authenticate only after Google has verified ownership.
    user = db.users.find_one({
        "email": email
    })

    if user:
        stored_google_sub = str(user.get("google_sub", "") or "").strip()
        if stored_google_sub and stored_google_sub != google_sub:
            app.logger.warning("Google subject mismatch for existing email account")
            flash("This Gmail account could not be linked safely. Please contact the site administrator.", "danger")
            return redirect(url_for("login"))

        db.users.update_one(
            {"id": user["id"]},
            {
                "$set": {
                    "google_sub": google_sub,
                    "google_verified": True,
                    "email_verified": True,
                    "auth_provider": "google"
                }
            }
        )

        session.clear()
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["email"] = email

        flash(
            "Login Successful!",
            "success"
        )

        return redirect("/dashboard")

    # Login and registration remain distinct actions.
    if auth_mode == "login":
        flash("No Flow Mate account exists for this Gmail yet. Please use Register first.", "warning")
        return redirect(url_for("register"))

    # New Google account: create the Flow Mate account automatically.
    username_base = email.split("@")[0]
    username = username_base

    counter = 1

    while db.users.find_one({
        "username": username
    }):
        counter += 1
        username = f"{username_base}{counter}"

    user_document = {
        "id": next_id("users"),
        "full_name": full_name,
        "email": email,
        "username": username,
        "password": None,
        "google_sub": google_sub,
        "google_verified": True,
        "email_verified": True,
        "auth_provider": "google",
        "created_at": india_now().strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    }

    try:
        db.users.insert_one(user_document)

    except DuplicateKeyError:
        # A simultaneous Google registration may have created the account.
        user = db.users.find_one({
            "email": email
        })

        if not user:
            flash(
                "Could not create your account. Please try again.",
                "danger"
            )
            return redirect("/register")

        session.clear()
        session["user_id"] = user["id"]
        session["username"] = user["username"]
        session["email"] = email

        flash(
            "Login Successful!",
            "success"
        )

        return redirect("/dashboard")

    session.clear()
    session["user_id"] = user_document["id"]
    session["username"] = user_document["username"]
    session["email"] = email

    flash(
        "Registration Successful! Welcome to Flow Mate.",
        "success"
    )

    return redirect("/dashboard")


# ==============================
# ADMIN PANEL
# ==============================

@app.route("/admin", methods=["GET"])
@admin_required
def admin_panel():
    db = get_db()
    users = []
    for user in db.users.find({}, {"_id": 0, "id": 1, "full_name": 1, "username": 1, "email": 1, "created_at": 1}):
        user_id = user.get("id")
        user["storage_bytes"] = get_user_storage_bytes(db, user_id)
        user["storage_text"] = format_bytes(user["storage_bytes"])
        user["is_admin"] = str(user.get("email", "")).lower() in ADMIN_EMAILS
        users.append(user)
    users.sort(key=lambda item: str(item.get("email", "")).lower())
    total_uploaded_bytes = sum(item["storage_bytes"] for item in users)

    try:
        storage_stats = get_database_storage_stats(db)
        storage_stats["estimated_text"] = format_bytes(storage_stats["estimated_bytes"])
        storage_stats["warning_text"] = format_bytes(MONGODB_WARNING_BYTES)
        storage_stats["stop_text"] = format_bytes(MONGODB_UPLOAD_STOP_BYTES)
        storage_error = None
    except Exception:
        app.logger.exception("Could not read MongoDB storage statistics for admin panel")
        storage_stats = None
        storage_error = "MongoDB storage statistics are temporarily unavailable. Check Atlas metrics for authoritative usage."

    session.setdefault("admin_csrf_token", secrets.token_urlsafe(32))
    return render_template(
        "admin.html",
        users=users,
        storage_stats=storage_stats,
        storage_error=storage_error,
        admin_csrf_token=session["admin_csrf_token"],
        user_count=len(users),
        total_uploaded_bytes=total_uploaded_bytes,
        total_uploaded_text=format_bytes(total_uploaded_bytes),
        format_bytes=format_bytes,
    )


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@admin_required
def admin_delete_user(user_id):
    submitted_token = request.form.get("csrf_token", "")
    expected_token = session.get("admin_csrf_token", "")
    if not expected_token or not submitted_token or not hmac.compare_digest(submitted_token, expected_token):
        abort(400, description="Invalid security token. Refresh the Admin Panel and try again.")

    db = get_db()
    target = db.users.find_one({"id": user_id})
    if not target:
        flash("User not found.", "warning")
        return redirect(url_for("admin_panel"))

    target_email = str(target.get("email", "")).strip().lower()
    if target_email in ADMIN_EMAILS:
        flash("Admin accounts cannot be deleted from this panel.", "danger")
        return redirect(url_for("admin_panel"))

    # Record file names first; do not remove files until database deletions succeed.
    notes = list(db.notes.find(
        {"user_id": user_id},
        {"_id": 0, "document_path": 1}
    ))
    stored_names = []
    for note in notes:
        stored_name = note.get("document_path")
        if not stored_name:
            continue
        safe_name = os.path.basename(str(stored_name))
        if safe_name == str(stored_name):
            stored_names.append(safe_name)
        else:
            app.logger.warning("Skipped unsafe stored upload path while deleting user id=%s", user_id)

    # Delete every collection that stores user-owned data, then the account itself.
    for collection_name in (
        "tasks", "notes", "expenses", "monthly_income",
        "category_budgets", "pomodoro_sessions"
    ):
        db[collection_name].delete_many({"user_id": user_id})

    db.users.delete_one({"id": user_id})

    # Clean the recorded files plus any orphaned uploads using this app's user-id prefix.
    names_to_remove = set(stored_names)
    try:
        for entry in os.scandir(app.config["UPLOAD_FOLDER"]):
            if entry.is_file() and entry.name.startswith(f"{user_id}_"):
                names_to_remove.add(entry.name)
    except OSError:
        app.logger.exception("Could not scan upload directory during account deletion")

    for safe_name in names_to_remove:
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], safe_name)
        try:
            if os.path.isfile(file_path):
                os.remove(file_path)
        except OSError:
            app.logger.exception("Could not remove uploaded file during user deletion: %s", safe_name)

    flash(
        f"User {target_email or user_id} and associated application records were deleted.",
        "success"
    )
    return redirect(url_for("admin_panel"))


@app.errorhandler(RequestEntityTooLarge)
def handle_request_too_large(error):
    if request.path == "/add_note":
        flash("The upload request is too large. Each document must be 500 KB or smaller.", "danger")
        return redirect(url_for("notes"))
    if request.path.startswith("/update_note/"):
        flash("The upload request is too large. Each document must be 500 KB or smaller.", "danger")
        note_id = request.path.rsplit("/", 1)[-1]
        if note_id.isdigit():
            return redirect(url_for("edit_note", id=int(note_id)))
        return redirect(url_for("notes"))
    return "Request too large. Please reduce the submitted data and try again.", 413


# ==============================
# DASHBOARD
# ==============================

@app.route("/dashboard")
def dashboard():

    if "user_id" not in session:
        flash("Please login first.", "warning")
        return redirect("/login")

    db = get_db()
    user_id = session["user_id"]

    user = db.users.find_one(
        {"id": user_id},
        {"_id": 0, "full_name": 1, "username": 1}
    ) or {}

    # ------------------------------
    # TASK STATISTICS
    # ------------------------------
    user_tasks = list(
        db.tasks.find(
            {"user_id": user_id},
            {"_id": 0, "status": 1}
        )
    )

    total_tasks = len(user_tasks)

    completed_tasks = sum(
        1 for task in user_tasks
        if str(task.get("status", "")).strip().lower()
        in {"completed", "complete", "done"}
    )

    pending_tasks = max(total_tasks - completed_tasks, 0)

    completion_rate = (
        round((completed_tasks / total_tasks) * 100)
        if total_tasks else 0
    )

    # ------------------------------
    # NOTES
    # ------------------------------
    notes_count = db.notes.count_documents(
        {"user_id": user_id}
    )

    # ------------------------------
    # POMODORO / FOCUS STATISTICS
    # ------------------------------
    today = india_now().date()
    today_string = today.strftime("%Y-%m-%d")

    today_sessions = list(
        db.pomodoro_sessions.find(
            {
                "user_id": user_id,
                "completed_at": {
                    "$regex": f"^{today_string}"
                }
            },
            {"_id": 0, "focus_minutes": 1}
        )
    )

    focus_minutes_today = sum(
        int(item.get("focus_minutes", 0) or 0)
        for item in today_sessions
    )

    sessions_today = len(today_sessions)

    total_focus_minutes = sum(
        int(item.get("focus_minutes", 0) or 0)
        for item in db.pomodoro_sessions.find(
            {"user_id": user_id},
            {"_id": 0, "focus_minutes": 1}
        )
    )

    total_focus_sessions = db.pomodoro_sessions.count_documents(
        {"user_id": user_id}
    )

    total_focus_hours = total_focus_minutes // 60
    total_focus_remaining_minutes = total_focus_minutes % 60

    # ------------------------------
    # LAST 7 DAYS FOCUS
    # ------------------------------
    week_focus = []

    for days_ago in range(6, -1, -1):

        current_day = today - timedelta(days=days_ago)
        current_day_string = current_day.strftime("%Y-%m-%d")

        day_sessions = db.pomodoro_sessions.find(
            {
                "user_id": user_id,
                "completed_at": {
                    "$regex": f"^{current_day_string}"
                }
            },
            {"_id": 0, "focus_minutes": 1}
        )

        day_minutes = sum(
            int(item.get("focus_minutes", 0) or 0)
            for item in day_sessions
        )

        week_focus.append({
            "label": current_day.strftime("%a"),
            "date": current_day.strftime("%d %b"),
            "minutes": day_minutes
        })

    max_week_focus = max(
        [item["minutes"] for item in week_focus] + [1]
    )

    for item in week_focus:
        item["percentage"] = round(
            (item["minutes"] / max_week_focus) * 100
        )

    return render_template(
        "dashboard.html",
        username=session["username"],
        full_name=user.get("full_name", session["username"]),
        total_tasks=total_tasks,
        completed_tasks=completed_tasks,
        pending_tasks=pending_tasks,
        completion_rate=completion_rate,
        notes_count=notes_count,
        focus_minutes_today=focus_minutes_today,
        sessions_today=sessions_today,
        total_focus_minutes=total_focus_minutes,
        total_focus_hours=total_focus_hours,
        total_focus_remaining_minutes=total_focus_remaining_minutes,
        total_focus_sessions=total_focus_sessions,
        week_focus=week_focus
    )


# ==============================
# LOGOUT
# ==============================

@app.route("/logout")
def logout():

    session.clear()

    flash("You have been logged out successfully.", "success")

    return redirect("/login")


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

@app.route("/delete_task/<int:id>", methods=["GET", "POST"])
def delete_task(id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    db = get_db()
    task = db.tasks.find_one({"id": id, "user_id": session["user_id"]})
    if not task:
        flash("Task not found.", "warning")
        return redirect(url_for("tasks"))

    if request.method == "GET":
        session.setdefault("delete_csrf_token", secrets.token_urlsafe(32))
        return render_template(
            "confirm_delete.html",
            item_label="task",
            item_name=task.get("title", "this task"),
            action_url=url_for("delete_task", id=id),
            return_url=url_for("tasks"),
            csrf_token=session["delete_csrf_token"],
        )

    token = request.form.get("csrf_token", "")
    expected = session.get("delete_csrf_token", "")
    if not expected or not token or not hmac.compare_digest(token, expected):
        abort(400, description="Invalid security token. Return to the list and try again.")

    db.tasks.delete_one({"id": id, "user_id": session["user_id"]})
    flash("Task deleted successfully.", "success")
    return redirect(url_for("tasks"))


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
    document_size = 0

    db = get_db()
    file = request.files.get("document")

    if file and file.filename != "":
        valid, document_size, error_message = validate_uploaded_file(
            file,
            db,
            session["user_id"]
        )

        if not valid:
            flash(error_message, "danger")
            return redirect("/notes")

        document_name = secure_filename(file.filename)

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

    db.notes.insert_one({
        "id": next_id("notes"),
        "user_id": session["user_id"],
        "title": title,
        "content": content,
        "document_name": document_name,
        "document_path": document_path,
        "document_size": document_size,
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

@app.route("/delete_note/<int:id>", methods=["GET", "POST"])
def delete_note(id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    db = get_db()
    note = document_to_dict(db.notes.find_one({"id": id, "user_id": session["user_id"]}))
    if note is None:
        flash("Note not found!", "warning")
        return redirect(url_for("notes"))

    if request.method == "GET":
        session.setdefault("delete_csrf_token", secrets.token_urlsafe(32))
        return render_template(
            "confirm_delete.html",
            item_label="note",
            item_name=note.get("title", "this note"),
            action_url=url_for("delete_note", id=id),
            return_url=url_for("notes"),
            csrf_token=session["delete_csrf_token"],
        )

    token = request.form.get("csrf_token", "")
    expected = session.get("delete_csrf_token", "")
    if not expected or not token or not hmac.compare_digest(token, expected):
        abort(400, description="Invalid security token. Return to the notes list and try again.")

    db.notes.delete_one({"id": id, "user_id": session["user_id"]})
    document_path = note.get("document_path")
    if document_path:
        safe_name = os.path.basename(str(document_path))
        if safe_name == str(document_path):
            file_path = os.path.join(app.config["UPLOAD_FOLDER"], safe_name)
            try:
                if os.path.isfile(file_path):
                    os.remove(file_path)
            except OSError:
                app.logger.exception("Could not remove note attachment after note deletion")

    flash("Note deleted successfully.", "success")
    return redirect(url_for("notes"))


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

    document_size = int(
        existing_note.get("document_size", 0) or 0
    )

    file = request.files.get("document")

    if file and file.filename != "":
        valid, new_document_size, error_message = validate_uploaded_file(
            file,
            db,
            session["user_id"],
            exclude_note_id=id
        )

        if not valid:
            flash(error_message, "danger")
            return redirect(f"/edit_note/{id}")

        old_file_path = None

        if document_path:
            safe_old_name = os.path.basename(str(document_path))
            if safe_old_name == str(document_path):
                old_file_path = os.path.join(app.config["UPLOAD_FOLDER"], safe_old_name)

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

        new_document_path = os.path.join(
            app.config["UPLOAD_FOLDER"],
            unique_filename
        )

        # Save the new file first. The old file is removed only after
        # the new file has been successfully written.
        file.save(new_document_path)

        if old_file_path and os.path.exists(old_file_path):
            os.remove(old_file_path)

        document_path = unique_filename
        document_size = new_document_size

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
                "document_path": document_path,
                "document_size": document_size
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

@app.route("/delete_expense/<int:id>", methods=["GET", "POST"])
def delete_expense(id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    db = get_db()
    expense = db.expenses.find_one({"id": id, "user_id": session["user_id"]})
    if not expense:
        flash("Expense not found.", "warning")
        return redirect(url_for("expenses"))

    if request.method == "GET":
        session.setdefault("delete_csrf_token", secrets.token_urlsafe(32))
        return render_template(
            "confirm_delete.html",
            item_label="expense",
            item_name=expense.get("description") or expense.get("category", "this expense"),
            action_url=url_for("delete_expense", id=id),
            return_url=url_for("expenses"),
            csrf_token=session["delete_csrf_token"],
        )

    token = request.form.get("csrf_token", "")
    expected = session.get("delete_csrf_token", "")
    if not expected or not token or not hmac.compare_digest(token, expected):
        abort(400, description="Invalid security token. Return to expenses and try again.")

    db.expenses.delete_one({"id": id, "user_id": session["user_id"]})
    flash("Expense deleted successfully.", "success")
    return redirect(url_for("expenses"))


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
