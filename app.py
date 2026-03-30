import os
import sqlite3
import uuid
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "dev-secret-key"

# -------------------------
# PATHS
# -------------------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "local_data")
os.makedirs(DATA_DIR, exist_ok=True)

DATABASE = os.path.join(DATA_DIR, "campus_jam.db")
UPLOAD_FOLDER = os.path.join(DATA_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

# -------------------------
# DB
# -------------------------
def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()

    conn.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS groups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        description TEXT,
        creator_id INTEGER
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS group_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        group_id INTEGER,
        user_id INTEGER
    )
    """)

    conn.execute("""
    CREATE TABLE IF NOT EXISTS messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sender_id INTEGER,
        receiver_id INTEGER,
        group_id INTEGER,
        content TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    conn.commit()
    conn.close()

# -------------------------
# AUTH
# -------------------------
def login_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrap

# -------------------------
# AUTH ROUTES
# -------------------------
@app.route("/signup", methods=["GET","POST"])
def signup():
    if request.method == "POST":
        username = request.form["username"]
        password = generate_password_hash(request.form["password"])

        conn = get_db_connection()
        conn.execute("INSERT INTO users (username,password) VALUES (?,?)", (username,password))
        conn.commit()
        conn.close()

        return redirect(url_for("login"))
    return render_template("signup.html")

@app.route("/login", methods=["GET","POST"])
def login():
    if request.method == "POST":
        username = request.form["username"]
        password = request.form["password"]

        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        conn.close()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            return redirect(url_for("groups"))

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# -------------------------
# GROUPS
# -------------------------
@app.route("/groups")
@login_required
def groups():
    conn = get_db_connection()

    groups = conn.execute("""
        SELECT groups.*, COUNT(group_members.id) as member_count
        FROM groups
        LEFT JOIN group_members ON groups.id = group_members.group_id
        GROUP BY groups.id
    """).fetchall()

    conn.close()
    return render_template("groups.html", groups=groups)

@app.route("/groups/create", methods=["GET","POST"])
@login_required
def create_group():
    if request.method == "POST":
        name = request.form["name"]
        description = request.form["description"]

        conn = get_db_connection()
        cursor = conn.execute(
            "INSERT INTO groups (name,description,creator_id) VALUES (?,?,?)",
            (name, description, session["user_id"])
        )

        group_id = cursor.lastrowid

        conn.execute(
            "INSERT INTO group_members (group_id,user_id) VALUES (?,?)",
            (group_id, session["user_id"])
        )

        conn.commit()
        conn.close()

        return redirect(url_for("group_chat", group_id=group_id))

    return render_template("create_group.html")

@app.route("/join_group/<int:group_id>", methods=["POST"])
@login_required
def join_group(group_id):
    conn = get_db_connection()

    conn.execute(
        "INSERT OR IGNORE INTO group_members (group_id,user_id) VALUES (?,?)",
        (group_id, session["user_id"])
    )

    conn.commit()
    conn.close()

    return redirect(url_for("group_chat", group_id=group_id))

# -------------------------
# GROUP CHAT
# -------------------------
@app.route("/group/<int:group_id>", methods=["GET","POST"])
@login_required
def group_chat(group_id):
    conn = get_db_connection()

    if request.method == "POST":
        content = request.form.get("content")

        conn.execute(
            "INSERT INTO messages (sender_id, group_id, content) VALUES (?,?,?)",
            (session["user_id"], group_id, content)
        )

        conn.commit()
        return redirect(url_for("group_chat", group_id=group_id))

    messages = conn.execute("""
        SELECT messages.*, users.username
        FROM messages
        JOIN users ON messages.sender_id = users.id
        WHERE messages.group_id = ?
        ORDER BY messages.created_at
    """, (group_id,)).fetchall()

    group = conn.execute("SELECT * FROM groups WHERE id=?", (group_id,)).fetchone()

    conn.close()

    return render_template("group_chat.html", messages=messages, group=group)

# -------------------------
# PRIVATE MESSAGES
# -------------------------
@app.route("/messages/<int:user_id>", methods=["GET","POST"])
@login_required
def private_messages(user_id):
    conn = get_db_connection()

    if request.method == "POST":
        content = request.form.get("content")

        conn.execute(
            "INSERT INTO messages (sender_id, receiver_id, content) VALUES (?,?,?)",
            (session["user_id"], user_id, content)
        )

        conn.commit()
        return redirect(url_for("private_messages", user_id=user_id))

    messages = conn.execute("""
        SELECT messages.*, users.username
        FROM messages
        JOIN users ON messages.sender_id = users.id
        WHERE (sender_id=? AND receiver_id=?)
           OR (sender_id=? AND receiver_id=?)
        ORDER BY messages.id
    """, (session["user_id"], user_id, user_id, session["user_id"])).fetchall()

    other_user = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()

    conn.close()

    return render_template("private_messages.html", messages=messages, other_user=other_user)

# -------------------------
# START
# -------------------------
init_db()

if __name__ == "__main__":
    app.run(debug=True)