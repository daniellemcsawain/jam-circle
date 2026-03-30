import os
import sqlite3
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "campus-jam-ultimate-key"

# --- CONFIG ---
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
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER, user_id INTEGER, content TEXT, username TEXT
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

# --- FEED & POSTS ---

@app.route("/")
@app.route("/home")
def home():
    conn = get_db_connection()
    posts = conn.execute("SELECT p.*, u.username, u.profile_image FROM posts p JOIN users u ON p.user_id = u.id ORDER BY p.created_at DESC").fetchall()
    comments = conn.execute("SELECT * FROM comments").fetchall()
    comments_by_post = {}
    for c in comments:
        comments_by_post.setdefault(c['post_id'], []).append(c)
    conn.close()
    return render_template("home.html", posts=posts, comments_by_post=comments_by_post)

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

@app.route("/post/delete/<int:post_id>", methods=["POST"])
@login_required
def delete_post(post_id):
    conn = get_db_connection()
    # Security: only delete if user owns the post
    conn.execute("DELETE FROM posts WHERE id = ? AND user_id = ?", (post_id, session["user_id"]))
    conn.commit()
    conn.close()
    flash("Post deleted successfully.")
    return redirect(request.referrer or url_for('home'))

@app.route("/post/comment/<int:post_id>", methods=["POST"])
@login_required
def add_comment(post_id):
    conn = get_db_connection()
    conn.execute("INSERT INTO comments (post_id, user_id, content, username) VALUES (?,?,?,?)", 
                 (post_id, session["user_id"], request.form.get("content"), session["username"]))
    conn.commit()
    conn.close()
    return redirect(request.referrer)

# --- USER PROFILES & SOCIAL ---

@app.route("/profile/<username>")
@login_required
def profile(username):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user: return "User not found", 404
    
    posts = conn.execute("SELECT * FROM posts WHERE user_id=?", (user["id"],)).fetchall()
    f_count = conn.execute("SELECT COUNT(*) FROM follows WHERE following_id=?", (user["id"],)).fetchone()[0]
    ing_count = conn.execute("SELECT COUNT(*) FROM follows WHERE follower_id=?", (user["id"],)).fetchone()[0]
    is_following = conn.execute("SELECT 1 FROM follows WHERE follower_id=? AND following_id=?", (session["user_id"], user["id"])).fetchone()
    
    # Comments for the profile view
    comments = conn.execute("SELECT * FROM comments").fetchall()
    comments_by_post = {}
    for c in comments:
        comments_by_post.setdefault(c['post_id'], []).append(c)
        
    conn.close()
    return render_template("profile.html", profile_user=user, posts=posts, follower_count=f_count, 
                           following_count=ing_count, is_following=is_following, comments_by_post=comments_by_post)

@app.route("/edit_profile", methods=["GET", "POST"])
@login_required
def edit_profile():
    conn = get_db_connection()
    if request.method == "POST":
        file = request.files.get('profile_image')
        img_name = None
        if file and file.filename != '':
            img_name = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], img_name))
            conn.execute("UPDATE users SET profile_image=? WHERE id=?", (img_name, session["user_id"]))
            
        conn.execute("UPDATE users SET major=?, instrument=?, favorite_genre=?, bio=? WHERE id=?", 
                     (request.form['major'], request.form['instrument'], request.form['favorite_genre'], request.form['bio'], session['user_id']))
        conn.commit()
        return redirect(url_for("profile", username=session['username']))
    
    user = conn.execute("SELECT * FROM users WHERE id=?", (session['user_id'],)).fetchone()
    conn.close()
    return render_template("edit_profile.html", user=user)

# --- MESSAGING ---

@app.route("/inbox")
@login_required
def inbox():
    conn = get_db_connection()
    conversations = conn.execute("""
        SELECT DISTINCT u.id, u.username FROM users u
        JOIN messages m ON (u.id = m.sender_id OR u.id = m.receiver_id)
        WHERE (m.sender_id = ? OR m.receiver_id = ?) AND u.id != ?
    """, (session["user_id"], session["user_id"], session["user_id"])).fetchall()
    conn.close()
    return render_template("inbox.html", conversations=conversations)

@app.route("/messages/user/<username>")
@login_required
def private_messages_by_username(username):
    conn = get_db_connection()
    user = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    if user:
        return redirect(url_for('private_messages', user_id=user['id']))
    flash("User not found.")
    return redirect(url_for('home'))

@app.route("/messages/<int:user_id>", methods=["GET", "POST"])
@login_required
def private_messages(user_id):
    conn = get_db_connection()
    if request.method == "POST":
        conn.execute("INSERT INTO messages (sender_id, receiver_id, content) VALUES (?,?,?)", 
                     (session["user_id"], user_id, request.form.get("content")))
        conn.commit()
    
    msgs = conn.execute("SELECT * FROM messages WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?) ORDER BY created_at", 
                        (session["user_id"], user_id, user_id, session["user_id"])).fetchall()
    other = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return render_template("private_messages.html", messages=msgs, other_user=other)

# --- JAM CIRCLES (GROUPS) ---

@app.route("/groups")
@login_required
def groups():
    conn = get_db_connection()
    g_list = conn.execute("SELECT g.*, (SELECT COUNT(*) FROM group_members WHERE group_id=g.id) as member_count FROM groups g").fetchall()
    conn.close()
    return render_template("groups.html", groups=g_list)

@app.route("/create_group", methods=["GET", "POST"])
@login_required
def create_group():
    if request.method == "POST":
        name = request.form.get('name')
        description = request.form.get('description')
        user_id = session.get('user_id')
        
        if not name or not description:
            flash("Please fill out all fields.")
            return redirect(url_for('create_group'))

        conn = get_db_connection()
        try:
            # Insert the group
            cur = conn.execute(
                "INSERT INTO groups (name, description, creator_id) VALUES (?, ?, ?)",
                (name, description, user_id)
            )
            group_id = cur.lastrowid
            # Automatically add the creator as the first member
            conn.execute(
                "INSERT INTO group_members (group_id, user_id) VALUES (?, ?)",
                (group_id, user_id)
            )
            conn.commit()
            flash(f"Group '{name}' created successfully!")
            return redirect(url_for("groups"))
        except Exception as e:
            conn.rollback()
            flash(f"Error creating group: {e}")
        finally:
            conn.close()
            
    return render_template("create_group.html")

@app.route("/group_chat/<int:group_id>", methods=["GET", "POST"])
@login_required
def group_chat(group_id):
    conn = get_db_connection()
    if request.method == "POST":
        conn.execute("INSERT INTO messages (sender_id, group_id, content) VALUES (?,?,?)", 
                     (session["user_id"], group_id, request.form.get("content")))
        conn.commit()
    group = conn.execute("SELECT * FROM groups WHERE id=?", (group_id,)).fetchone()
    msgs = conn.execute("SELECT m.*, u.username FROM messages m JOIN users u ON m.sender_id = u.id WHERE m.group_id=? ORDER BY created_at", (group_id,)).fetchall()
    conn.close()
    return render_template("group_chat.html", group=group, messages=msgs)

@app.route("/join_group/<int:group_id>", methods=["POST"])
@login_required
def join_group(group_id):
    conn = get_db_connection()
    conn.execute("INSERT OR IGNORE INTO group_members (group_id, user_id) VALUES (?,?)", (group_id, session["user_id"]))
    conn.commit()
    conn.close()
    return redirect(url_for("group_chat", group_id=group_id))

# --- DISCOVERY & SEARCH ---

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

@app.route("/search_groups")
@login_required
def search_groups():
    return redirect(url_for('groups'))

# --- AUTH & HELPERS ---

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE username=?", (request.form['username'],)).fetchone()
        conn.close()
        if user and check_password_hash(user['password'], request.form['password']):
            session.update({"user_id": user['id'], "username": user['username']})
            return redirect(url_for("home"))
        flash("Invalid login credentials.")
    return render_template("login.html")

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        pw = generate_password_hash(request.form['password'])
        try:
            conn = get_db_connection()
            conn.execute("INSERT INTO users (username, password, major, instrument, favorite_genre, bio) VALUES (?,?,?,?,?,?)",
                         (request.form['username'], pw, request.form['major'], request.form['instrument'], request.form['favorite_genre'], request.form['bio']))
            conn.commit()
            conn.close()
            return redirect(url_for("login"))
        except: flash("Username already exists.")
    return render_template("signup.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# PLACEHOLDERS FOR FOLLOWER LISTS
@app.route("/followers/<username>")
@login_required
def followers_list(username):
    conn = get_db_connection()
    user = conn.execute("SELECT id, username FROM users WHERE username=?", (username,)).fetchone()
    users = conn.execute("SELECT u.* FROM users u JOIN follows f ON u.id = f.follower_id WHERE f.following_id=?", (user['id'],)).fetchall()
    return render_template("followers_list.html", profile_user=user, users=users)

@app.route("/following/<username>")
@login_required
def following_list(username):
    conn = get_db_connection()
    user = conn.execute("SELECT id, username FROM users WHERE username=?", (username,)).fetchone()
    users = conn.execute("SELECT u.* FROM users u JOIN follows f ON u.id = f.following_id WHERE f.follower_id=?", (user['id'],)).fetchall()
    return render_template("following_list.html", profile_user=user, users=users)

@app.route("/like_post/<int:post_id>", methods=["POST"])
@login_required
def like_post(post_id):
    return redirect(request.referrer) # Simple placeholder

@app.route("/delete_message/<int:message_id>", methods=["POST"])
@login_required
def delete_message(message_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM messages WHERE id=? AND sender_id=?", (message_id, session["user_id"]))
    conn.commit()
    conn.close()
    return redirect(request.referrer)

init_db()
if __name__ == "__main__":
    app.run(debug=True)