import os
import sqlite3
from functools import wraps
from flask import Flask, render_template, request, redirect, url_for, session, flash, send_from_directory
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = "campus-jam-final-sync-v2"

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

# --- AUTH ---
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
        except: flash("Username taken.")
    return render_template("signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        conn = get_db_connection()
        user = conn.execute("SELECT * FROM users WHERE username=?", (request.form['username'],)).fetchone()
        conn.close()
        if user and check_password_hash(user['password'], request.form['password']):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            return redirect(url_for("home"))
        flash("Invalid Credentials")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))

# --- POSTS ---
@app.route("/")
@app.route("/home")
def home():
    conn = get_db_connection()
    # Note: clip_url matches the database column
    posts = conn.execute("SELECT p.*, u.username, u.profile_image, u.instrument, u.major FROM posts p JOIN users u ON p.user_id = u.id ORDER BY p.created_at DESC").fetchall()
    comments = conn.execute("SELECT * FROM comments").fetchall()
    comments_dict = {}
    for c in comments: comments_dict.setdefault(c['post_id'], []).append(c)
    conn.close()
    return render_template("home.html", posts=posts, comments_by_post=comments_dict)

@app.route("/post", methods=["GET", "POST"])
@login_required
def post():
    if request.method == "POST":
        file = request.files.get('clip')
        filename = secure_filename(file.filename) if file and file.filename != '' else None
        if filename: file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        conn = get_db_connection()
        conn.execute("INSERT INTO posts (user_id, caption, clip_url) VALUES (?,?,?)", (session["user_id"], request.form.get("caption"), filename))
        conn.commit()
        conn.close()
        return redirect(url_for("home"))
    return render_template("post.html")

@app.route("/<int:post_id>", methods=["POST"])
@login_required
def delete_post(post_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM posts WHERE id=? AND user_id=?", (post_id, session["user_id"]))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('home'))

@app.route("/post/comment/<int:post_id>", methods=["POST"])
@login_required
def add_comment(post_id):
    conn = get_db_connection()
    conn.execute("INSERT INTO comments (post_id, user_id, content, username) VALUES (?,?,?,?)", (post_id, session["user_id"], request.form.get("content"), session["username"]))
    conn.commit()
    conn.close()
    return redirect(request.referrer or url_for('home'))

@app.route("/like_post/<int:post_id>", methods=["POST"])
@login_required
def like_post(post_id):
    return redirect(request.referrer or url_for('home')) # Placeholder for like logic

# --- PROFILE & FOLLOWS ---
# --- FIX FOR GROUPS ---
@app.route("/groups")
@login_required
def groups():
    conn = get_db_connection()
    # Updated query to prevent the .get() error in your logs
    g_list = conn.execute("""
        SELECT g.*, (SELECT COUNT(*) FROM group_members WHERE group_id = g.id) as member_count 
        FROM groups g
    """).fetchall()
    conn.close()
    return render_template("groups.html", groups=g_list)


# --- FIX FOR PROFILE ---
@app.route("/profile/<username>")
@login_required
def profile(username):
    conn = get_db_connection()
    user = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    if not user:
        conn.close()
        return "User not found", 404
    
    # Fetch posts and comments so the profile doesn't crash
    posts = conn.execute("SELECT * FROM posts WHERE user_id=?", (user["id"],)).fetchall()
    comments = conn.execute("SELECT * FROM comments").fetchall()
    comments_dict = {}
    for c in comments:
        comments_dict.setdefault(c['post_id'], []).append(c)
        
    conn.close()
    return render_template("profile.html", profile_user=user, posts=posts, comments_by_post=comments_dict)

@app.route("/follow/<int:user_id>", methods=["POST"])
@login_required
def follow_user(user_id):
    conn = get_db_connection()
    try: conn.execute("INSERT INTO follows (follower_id, following_id) VALUES (?,?)", (session['user_id'], user_id))
    except: pass
    conn.commit()
    conn.close()
    return redirect(request.referrer)

@app.route("/unfollow/<int:user_id>", methods=["POST"])
@login_required
def unfollow_user(user_id):
    conn = get_db_connection()
    conn.execute("DELETE FROM follows WHERE follower_id=? AND following_id=?", (session['user_id'], user_id))
    conn.commit()
    conn.close()
    return redirect(request.referrer)

# --- MESSAGES ---
@app.route("/messages/user/<username>")
@login_required
def private_messages_by_username(username):
    conn = get_db_connection()
    user = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
    conn.close()
    return redirect(url_for('private_messages', user_id=user['id'])) if user else redirect(url_for('home'))

@app.route("/messages/<int:user_id>", methods=["GET", "POST"])
@login_required
def private_messages(user_id):
    conn = get_db_connection()
    if request.method == "POST":
        conn.execute("INSERT INTO messages (sender_id, receiver_id, content) VALUES (?,?,?)", (session["user_id"], user_id, request.form.get("content")))
        conn.commit()
    msgs = conn.execute("SELECT * FROM messages WHERE (sender_id=? AND receiver_id=?) OR (sender_id=? AND receiver_id=?) ORDER BY created_at", (session["user_id"], user_id, user_id, session["user_id"])).fetchall()
    other = conn.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
    conn.close()
    return render_template("private_messages.html", messages=msgs, other_user=other)

# --- GROUPS ---

@app.route("/create_group", methods=["GET", "POST"])
@login_required
def create_group():
    if request.method == "POST":
        conn = get_db_connection()
        # Creates the group
        cur = conn.execute("INSERT INTO groups (name, description, creator_id) VALUES (?,?,?)", 
                         (request.form['name'], request.form['description'], session['user_id']))
        # Automatically adds the creator as the first member
        conn.execute("INSERT INTO group_members (group_id, user_id) VALUES (?,?)", 
                    (cur.lastrowid, session['user_id']))
        conn.commit()
        conn.close()
        # SUCCESSFUL FIX: Redirect to the 'groups' function name
        return redirect(url_for("groups")) 
    return render_template("create_group.html")
@app.route("/search_groups")
@login_required
def search_groups():
    query = request.args.get("q", "")

    conn = get_db_connection()

    groups = conn.execute("""
        SELECT id, name, description
        FROM groups
        WHERE name LIKE ?
    """, (f"%{query}%",)).fetchall()

    conn.close()

    return render_template("groups.html", groups=groups)

@app.route("/groups")
@login_required
def groups():
    conn = get_db_connection()
    # Updated query to prevent the .get() error in your logs
    g_list = conn.execute("""
        SELECT g.*, (SELECT COUNT(*) FROM group_members WHERE group_id = g.id) as member_count 
        FROM groups g
    """).fetchall()
    conn.close()
    return render_template("groups.html", groups=g_list)

@app.route("/group_chat/<int:group_id>", methods=["GET", "POST"])
@login_required
def group_chat(group_id):
    conn = get_db_connection()
    
    if request.method == "POST":
        content = request.form.get("content")
        file = request.files.get("chat_image") # Look for the image file
        filename = None
        
        if file and file.filename != '':
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        # Insert message into database with the filename
        conn.execute(
            "INSERT INTO messages (sender_id, group_id, content, file_name) VALUES (?, ?, ?, ?)",
            (session["user_id"], group_id, content, filename)
        )
        conn.commit()
    
    # Get the group details and messages
    group = conn.execute("SELECT * FROM groups WHERE id = ?", (group_id,)).fetchone()
    messages = conn.execute("""
        SELECT m.*, u.username 
        FROM messages m 
        JOIN users u ON m.sender_id = u.id 
        WHERE m.group_id = ? 
        ORDER BY m.created_at ASC
    """, (group_id,)).fetchall()
    
    conn.close()
    return render_template("groups", group=group, messages=messages)

@app.route("/join_group/<int:group_id>", methods=["POST"])
@login_required
def join_group(group_id):
    conn = get_db_connection()
    try:
        # This adds the student to the group
        conn.execute("INSERT OR IGNORE INTO group_members (group_id, user_id) VALUES (?, ?)", 
                     (group_id, session["user_id"]))
        conn.commit()
        flash("You joined the circle!")
    except Exception as e:
        flash("Could not join group.")
    finally:
        conn.close()
    return redirect(url_for("groups"))

# --- UTILS ---
@app.route("/search")
@login_required
def search():
    q = request.args.get('q', '')
    conn = get_db_connection()
    users = conn.execute("SELECT * FROM users WHERE username LIKE ? OR instrument LIKE ?", (f'%{q}%', f'%{q}%')).fetchall() if q else []
    conn.close()
    return render_template("search.html", users=users, query=q)

@app.route('/uploads/<filename>')
def uploaded_file(filename): return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# Templates check
@app.route("/edit_profile", methods=["GET", "POST"])
@login_required
def edit_profile(): return render_template("edit_profile.html", user={"major":"", "instrument":"", "favorite_genre":"", "bio":""})

@app.route("/inbox")
@login_required
def inbox(): return render_template("inbox.html", conversations=[])

@app.route("/followers/<username>")
@login_required
def followers_list(username): return render_template("followers_list.html", profile_user={"username":username}, users=[])

@app.route("/following/<username>")
@login_required
def following_list(username): return render_template("following_list.html", profile_user={"username":username}, users=[])


init_db()
if __name__ == "__main__":
    app.run(debug=True)