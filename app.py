import os
import sqlite3
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "campus-jam-secure-key"

# CONFIG
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DATA_DIR = os.path.join(BASE_DIR, "local_data")
UPLOAD_FOLDER = os.path.join(DATA_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
DATABASE = os.path.join(DATA_DIR, "campus_jam.db")

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db_connection()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT, username TEXT UNIQUE, password TEXT, 
            major TEXT, instrument TEXT, favorite_genre TEXT, bio TEXT, profile_image TEXT
        );
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, description TEXT, creator_id INTEGER
        );
        CREATE TABLE IF NOT EXISTS group_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT, group_id INTEGER, user_id INTEGER, UNIQUE(group_id, user_id)
        );
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, caption TEXT, clip_url TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT, sender_id INTEGER, receiver_id INTEGER, 
            group_id INTEGER, content TEXT, file_name TEXT, file_type TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS follows (
            follower_id INTEGER, following_id INTEGER, PRIMARY KEY(follower_id, following_id)
        );
    """)
    conn.commit()
    conn.close()

def login_required(f):
    @wraps(f)
    def wrap(*args, **kwargs):
        if "user_id" not in session: return redirect(url_for("login"))
        return f(*args, **kwargs)
    return wrap

# --- ROUTES ---

@app.route("/")
@app.route("/home")
def home():
    conn = get_db_connection()
    posts = conn.execute("SELECT p.*, u.username, u.profile_image FROM posts p JOIN users u ON p.user_id = u.id ORDER BY p.created_at DESC").fetchall()
    conn.close()
    return render_template("home.html", posts=posts)

@app.route("/post", methods=["GET", "POST"])
@login_required
def post():
    if request.method == "POST":
        file = request.files.get('clip')
        filename = None
        if file and file.filename != '':
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        
        conn = get_db_connection()
        conn.execute("INSERT INTO posts (user_id, caption, clip_url) VALUES (?,?,?)", 
                     (session["user_id"], request.form.get("caption"), filename))
        conn.commit()
        conn.close()
        return redirect(url_for("home"))
    return render_template("post.html")

@app.route("/profile/<username>")
@login_required
def profile(username):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user: return "User not found", 404
    posts = conn.execute("SELECT * FROM posts WHERE user_id=?", (user["id"],)).fetchall()
    followers = conn.execute("SELECT COUNT(*) FROM follows WHERE following_id=?", (user["id"],)).fetchone()[0]
    following = conn.execute("SELECT COUNT(*) FROM follows WHERE follower_id=?", (user["id"],)).fetchone()[0]
    is_following = conn.execute("SELECT 1 FROM follows WHERE follower_id=? AND following_id=?", (session["user_id"], user["id"])).fetchone()
    conn.close()
    return render_template("profile.html", profile_user=user, posts=posts, follower_count=followers, following_count=following, is_following=is_following)

@app.route("/search")
@login_required
def search():
    q = request.args.get('q', '')
    users = []
    if q:
        conn = get_db_connection()
        users = conn.execute("SELECT * FROM users WHERE username LIKE ? OR instrument LIKE ? OR major LIKE ?", (f'%{q}%', f'%{q}%', f'%{q}%')).fetchall()
        conn.close()
    return render_template("search.html", users=users, query=q)

@app.route("/group_chat/<int:group_id>", methods=["GET", "POST"])
@login_required
def group_chat(group_id):
    conn = get_db_connection()
    if request.method == "POST":
        conn.execute("INSERT INTO messages (sender_id, group_id, content) VALUES (?,?,?)", 
                     (session["user_id"], group_id, request.form.get("content")))
        conn.commit()
    
    group = conn.execute("SELECT * FROM groups WHERE id=?", (group_id,)).fetchone()
    messages = conn.execute("SELECT m.*, u.username FROM messages m JOIN users u ON m.sender_id = u.id WHERE m.group_id=? ORDER BY created_at", (group_id,)).fetchall()
    conn.close()
    return render_template("group_chat.html", group=group, messages=messages)

@app.route("/create_group", methods=["GET", "POST"])
@login_required
def create_group():
    if request.method == "POST":
        conn = get_db_connection()
        cur = conn.execute("INSERT INTO groups (name, description, creator_id) VALUES (?,?,?)", (request.form['name'], request.form['description'], session['user_id']))
        conn.execute("INSERT INTO group_members (group_id, user_id) VALUES (?,?)", (cur.lastrowid, session['user_id']))
        conn.commit()
        conn.close()
        return redirect(url_for("groups"))
    return render_template("create_group.html")

# AUTH
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        hashed_pw = generate_password_hash(request.form['password'])
        try:
            conn = get_db_connection()
            conn.execute("INSERT INTO users (username, password, major, instrument) VALUES (?,?,?,?)", 
                         (request.form['username'], hashed_pw, request.form['major'], request.form['instrument']))
            conn.commit()
            return redirect(url_for("login"))
        except: flash("Username taken")
    return render_template("signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE username=?", (request.form['username'],)).fetchone()
        if user and check_password_hash(user['password'], request.form['password']):
            session.update({"user_id": user['id'], "username": user['username']})
            return redirect(url_for("home"))
        flash("Invalid Credentials")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

init_db()
if __name__ == "__main__":
    app.run(debug=True)