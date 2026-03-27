
import os
import sqlite3
import uuid
from functools import wraps

from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    send_from_directory,
)
from flask_socketio import SocketIO, join_room
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename


app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "dev-secret-key")
socketio = SocketIO(app, async_mode="threading")


# -------------------------
# STORAGE SETUP
# -------------------------

BASE_DIR = os.path.abspath(os.path.dirname(__file__))

if os.environ.get("RENDER"):
    DATA_DIR = os.path.join(BASE_DIR, "data")
else:
    DATA_DIR = os.path.join(BASE_DIR, "local_data")

os.makedirs(DATA_DIR, exist_ok=True)

DATABASE = os.path.join(DATA_DIR, "campus_jam.db")
UPLOAD_FOLDER = os.path.join(DATA_DIR, "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {
    "mp4", "mov", "webm",
    "mp3", "wav", "m4a",
    "png", "jpg", "jpeg", "gif",
    "pdf", "doc", "docx", "txt"
}

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024


# -------------------------
# DATABASE HELPERS
# -------------------------

def get_db_connection():
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def add_column_if_missing(conn, table_name, column_name, column_definition):
    cursor = conn.execute(f"PRAGMA table_info({table_name})")
    columns = [row[1] for row in cursor.fetchall()]
    if column_name not in columns:
        conn.execute(
            f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_definition}"
        )
        conn.commit()


def init_db():
    conn = sqlite3.connect(DATABASE)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL UNIQUE,
            password TEXT NOT NULL,
            major TEXT DEFAULT '',
            instrument TEXT DEFAULT '',
            favorite_genre TEXT DEFAULT '',
            bio TEXT DEFAULT '',
            profile_image TEXT DEFAULT ''
        )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS group_members (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    group_id INTEGER
)
""")
    
    cursor.execute ("""
    CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    sender_id INTEGER NOT NULL,
    receiver_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
) """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            caption TEXT DEFAULT '',
            clip_filename TEXT DEFAULT '',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (post_id) REFERENCES posts (id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS likes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            post_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            UNIQUE(post_id, user_id),
            FOREIGN KEY (post_id) REFERENCES posts (id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS follows (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            follower_id INTEGER NOT NULL,
            following_id INTEGER NOT NULL,
            UNIQUE(follower_id, following_id),
            FOREIGN KEY (follower_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (following_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS groups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT NOT NULL,
            creator_id INTEGER,
            FOREIGN KEY (creator_id) REFERENCES users (id) ON DELETE SET NULL
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS group_members (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id INTEGER NOT NULL,
            user_id INTEGER NOT NULL,
            UNIQUE(group_id, user_id),
            FOREIGN KEY (group_id) REFERENCES groups (id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            receiver_id INTEGER,
            group_id INTEGER,
             TEXT,
            file_name TEXT,
            file_type TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (sender_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (receiver_id) REFERENCES users (id) ON DELETE CASCADE,
            FOREIGN KEY (group_id) REFERENCES groups (id) ON DELETE CASCADE
        )
    """)

    conn.commit()

    add_column_if_missing(conn, "users", "major", "TEXT DEFAULT ''")
    add_column_if_missing(conn, "users", "instrument", "TEXT DEFAULT ''")
    add_column_if_missing(conn, "users", "favorite_genre", "TEXT DEFAULT ''")
    add_column_if_missing(conn, "users", "bio", "TEXT DEFAULT ''")
    add_column_if_missing(conn, "users", "profile_image", "TEXT DEFAULT ''")
    add_column_if_missing(conn, "posts", "caption", "TEXT DEFAULT ''")
    add_column_if_missing(conn, "posts", "clip_filename", "TEXT DEFAULT ''")

    existing_groups = conn.execute("SELECT COUNT(*) FROM groups").fetchone()[0]

    if existing_groups == 0:
        conn.executemany("""
            INSERT INTO groups (name, description, creator_id)
            VALUES (?, ?, ?)
        """, [
            ("Acoustic Sunset Circle", "Chill acoustic jams around campus.", None),
            ("Jazz & Improv Collective", "For students into jazz and improvisation.", None),
            ("Band Finder", "Find singers, drummers, guitarists, and bass players.", None),
            ("Dorm Jam Sessions", "Meet other students in the dorms who want to jam.", None),
        ])
        conn.commit()

    conn.close()


# -------------------------
# FILE HELPERS
# -------------------------

def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


def save_uploaded_file(file_storage):
    if not file_storage or not file_storage.filename:
        return ""

    if not allowed_file(file_storage.filename):
        return None

    original_name = secure_filename(file_storage.filename)
    ext = original_name.rsplit(".", 1)[1].lower()
    unique_name = f"{uuid.uuid4().hex}.{ext}"
    full_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
    file_storage.save(full_path)
    return unique_name


# -------------------------
# AUTH HELPERS
# -------------------------

def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.")
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapped_view


# -------------------------
# POST HELPERS
# -------------------------

def fetch_posts_with_counts(conn, user_id=None, only_username=None):
    params = []
    where_clause = ""

    if only_username:
        where_clause = "WHERE users.username = ?"
        params.append(only_username)

    posts = conn.execute(f"""
        SELECT
            posts.id,
            posts.user_id,
            posts.caption,
            posts.clip_filename,
            posts.created_at,
            users.username,
            users.major,
            users.instrument,
            users.profile_image,
            COALESCE(like_counts.like_count, 0) AS like_count,
            COALESCE(comment_counts.comment_count, 0) AS comment_count
        FROM posts
        JOIN users ON posts.user_id = users.id
        LEFT JOIN (
            SELECT post_id, COUNT(*) AS like_count
            FROM likes
            GROUP BY post_id
        ) AS like_counts ON posts.id = like_counts.post_id
        LEFT JOIN (
            SELECT post_id, COUNT(*) AS comment_count
            FROM comments
            GROUP BY post_id
        ) AS comment_counts ON posts.id = comment_counts.post_id
        {where_clause}
        ORDER BY posts.id DESC
    """, params).fetchall()

    post_ids = [post["id"] for post in posts]

    comments_by_post = {}
    if post_ids:
        placeholders = ",".join("?" for _ in post_ids)
        comments = conn.execute(f"""
            SELECT
                comments.*,
                users.username
            FROM comments
            JOIN users ON comments.user_id = users.id
            WHERE comments.post_id IN ({placeholders})
            ORDER BY comments.id ASC
        """, post_ids).fetchall()

        for comment in comments:
            comments_by_post.setdefault(comment["post_id"], []).append(comment)

    liked_post_ids = set()
    if user_id and post_ids:
        placeholders = ",".join("?" for _ in post_ids)
        liked_rows = conn.execute(f"""
            SELECT post_id
            FROM likes
            WHERE user_id = ? AND post_id IN ({placeholders})
        """, [user_id, *post_ids]).fetchall()
        liked_post_ids = {row["post_id"] for row in liked_rows}

    return posts, comments_by_post, liked_post_ids

    content = request.form.get("content")

    if not content:
        flash("Message can't be empty")
    return redirect(url_for("messages", user_id=user_id))

    c.execute("""
    INSERT INTO messages (sender_id, receiver_id, content)
    VALUES (?, ?, ?)
""", (sender_id, user_id, content))

# -------------------------
# BASIC ROUTES
# -------------------------

@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)


@app.route("/")
def home():
    conn = get_db_connection()
    user_id = session.get("user_id")

    posts = conn.execute("""
        SELECT
            posts.*,
            users.username,
            users.profile_image,

            (SELECT COUNT(*) FROM likes WHERE likes.post_id = posts.id) AS like_count,
            (SELECT COUNT(*) FROM comments WHERE comments.post_id = posts.id) AS comment_count

        FROM posts
        JOIN users ON posts.user_id = users.id
        ORDER BY posts.id DESC
    """).fetchall()

    liked_post_ids = set()

    if user_id:
        liked = conn.execute(
            "SELECT post_id FROM likes WHERE user_id = ?",
            (user_id,)
        ).fetchall()

        liked_post_ids = {row["post_id"] for row in liked}

    comments_by_post = {}
    comments = conn.execute("""
        SELECT comments.*, users.username
        FROM comments
        JOIN users ON comments.user_id = users.id
        ORDER BY comments.id ASC
    """).fetchall()

    for c in comments:
        comments_by_post.setdefault(c["post_id"], []).append(c)

    conn.close()

    return render_template(
        "home.html",
        posts=posts,
        liked_post_ids=liked_post_ids,
        comments_by_post=comments_by_post
    )


# -------------------------
# AUTH ROUTES
# -------------------------

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()
        major = request.form.get("major", "").strip()
        instrument = request.form.get("instrument", "").strip()
        favorite_genre = request.form.get("favorite_genre", "").strip()

        if not username:
            flash("Username is required.")
            return redirect(url_for("signup"))

        if not password:
            flash("Password is required.")
            return redirect(url_for("signup"))

        hashed_password = generate_password_hash(password)

        conn = get_db_connection()
        try:
            conn.execute("""
                INSERT INTO users (username, password, major, instrument, favorite_genre)
                VALUES (?, ?, ?, ?, ?)
            """, (username, hashed_password, major, instrument, favorite_genre))
            conn.commit()
            flash("Account created successfully. Please log in.")
            return redirect(url_for("login"))
        except sqlite3.IntegrityError:
            flash("That username is already taken.")
            return redirect(url_for("signup"))
        finally:
            conn.close()

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        conn = get_db_connection()
        user = conn.execute("""
            SELECT *
            FROM users
            WHERE username = ?
        """, (username,)).fetchone()
        conn.close()

        if user and check_password_hash(user["password"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            flash("Logged in successfully.")
            return redirect(url_for("home"))

        flash("Invalid username or password.")
        return redirect(url_for("login"))

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    flash("Logged out.")
    return redirect(url_for("login"))


# -------------------------
# POST ROUTES
# -------------------------

@app.route("/post", methods=["GET", "POST"])
@login_required
def post():
    if request.method == "POST":
        caption = request.form.get("caption", "").strip()
        clip = request.files.get("clip")

        clip_filename = ""

        if clip and clip.filename:
            saved_name = save_uploaded_file(clip)
            if saved_name is None:
                flash("That file type is not allowed.")
                return redirect(url_for("post"))
            clip_filename = saved_name

        if not caption and not clip_filename:
            flash("Add a caption or upload a file.")
            return redirect(url_for("post"))

        conn = get_db_connection()
        conn.execute("""
            INSERT INTO posts (user_id, caption, clip_filename)
            VALUES (?, ?, ?)
        """, (session["user_id"], caption, clip_filename))
        conn.commit()
        conn.close()

        flash("Post uploaded successfully!")
        return redirect(url_for("home"))

    return render_template("post.html")


@app.route("/create_post", methods=["POST"])
@login_required
def create_post():
    caption = request.form.get("caption", "").strip()
    clip = request.files.get("clip")

    clip_filename = ""

    if clip and clip.filename:
        saved_name = save_uploaded_file(clip)
        if saved_name is None:
            flash("That file type is not allowed.")
            return redirect(url_for("home"))
        clip_filename = saved_name

    if not caption and not clip_filename:
        flash("Add a caption or upload a file.")
        return redirect(url_for("home"))

    conn = get_db_connection()
    conn.execute("""
        INSERT INTO posts (user_id, caption, clip_filename)
        VALUES (?, ?, ?)
    """, (session["user_id"], caption, clip_filename))
    conn.commit()
    conn.close()

    flash("Post created.")
    return redirect(url_for("home"))


@app.route("/delete_post/<int:post_id>", methods=["POST"])
@login_required
def delete_post(post_id):
    conn = get_db_connection()

    post = conn.execute("""
        SELECT *
        FROM posts
        WHERE id = ?
    """, (post_id,)).fetchone()

    if not post:
        conn.close()
        flash("Post not found.")
        return redirect(url_for("home"))

    if post["user_id"] != session["user_id"]:
        conn.close()
        flash("You can only delete your own posts.")
        return redirect(url_for("home"))

    if post["clip_filename"]:
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], post["clip_filename"])
        if os.path.exists(file_path):
            os.remove(file_path)

    conn.execute("DELETE FROM posts WHERE id = ?", (post_id,))
    conn.commit()
    conn.close()

    flash("Post deleted.")
    return redirect(url_for("home"))


# -------------------------
# COMMENT + LIKE ROUTES
# -------------------------

@app.route("/comment/<int:post_id>", methods=["POST"])
@login_required
def add_comment(post_id):
    content = request.form.get("content", "").strip()

    if not content:
        flash("Comment cannot be empty")
        return redirect(request.referrer or url_for("home"))

    conn = get_db_connection()

    # 🔥 CHECK POST EXISTS
    post = conn.execute(
        "SELECT id FROM posts WHERE id = ?",
        (post_id,)
    ).fetchone()

    if not post:
        conn.close()
        flash("Post does not exist")
        return redirect(url_for("home"))

    # 🔥 CHECK USER EXISTS
    user = conn.execute(
        "SELECT id FROM users WHERE id = ?",
        (session["user_id"],)
    ).fetchone()

    if not user:
        conn.close()
        flash("User not found — please log in again")
        return redirect(url_for("login"))

    # ✅ SAFE INSERT
    conn.execute(
        "INSERT INTO comments (post_id, user_id, content) VALUES (?, ?, ?)",
        (post_id, session["user_id"], content)
    )

    conn.commit()
    conn.close()

    return redirect(request.referrer or url_for("home"))


@app.route("/comment/delete/<int:comment_id>", methods=["POST"])
@login_required
def delete_comment(comment_id):
    conn = get_db_connection()

    comment = conn.execute(
        "SELECT * FROM comments WHERE id = ?",
        (comment_id,)
    ).fetchone()

    if not comment:
        conn.close()
        flash("Comment not found")
        return redirect(url_for("home"))

    # Only allow owner to delete
    if comment["user_id"] != session["user_id"]:
        conn.close()
        flash("Not allowed")
        return redirect(url_for("home"))

    conn.execute(
        "DELETE FROM comments WHERE id = ?",
        (comment_id,)
    )

    conn.commit()
    conn.close()

    return redirect(request.referrer or url_for("home"))


@app.route("/like/<int:post_id>", methods=["POST"])
@login_required
def like_post(post_id):
    conn = get_db_connection()

    try:
        conn.execute(
            "INSERT INTO likes (post_id, user_id) VALUES (?, ?)",
            (post_id, session["user_id"])
        )
        conn.commit()
    except:
        pass

    conn.close()
    return redirect(request.referrer or url_for("home"))


@app.route("/unlike/<int:post_id>", methods=["POST"])
@login_required
def unlike_post(post_id):
    conn = get_db_connection()

    conn.execute(
        "DELETE FROM likes WHERE post_id = ? AND user_id = ?",
        (post_id, session["user_id"])
    )

    conn.commit()
    conn.close()

    return redirect(request.referrer or url_for("home"))


# -------------------------
# PROFILE ROUTES
# -------------------------

@app.route("/profile/<username>")
def profile(username):
    conn = get_db_connection()

    user = conn.execute("""
        SELECT *
        FROM users
        WHERE username = ?
    """, (username,)).fetchone()

    if not user:
        conn.close()
        flash("User not found.")
        return redirect(url_for("home"))

    posts, comments_by_post, liked_post_ids = fetch_posts_with_counts(
        conn,
        user_id=session.get("user_id"),
        only_username=username
    )

    follower_count = conn.execute("""
        SELECT COUNT(*)
        FROM follows
        WHERE following_id = ?
    """, (user["id"],)).fetchone()[0]

    following_count = conn.execute("""
        SELECT COUNT(*)
        FROM follows
        WHERE follower_id = ?
    """, (user["id"],)).fetchone()[0]

    is_following = False
    if "user_id" in session:
        follow_row = conn.execute("""
            SELECT 1
            FROM follows
            WHERE follower_id = ? AND following_id = ?
        """, (session["user_id"], user["id"])).fetchone()
        is_following = follow_row is not None

    conn.close()

    return render_template(
        "profile.html",
        profile_user=user,
        posts=posts,
        comments_by_post=comments_by_post,
        liked_post_ids=liked_post_ids,
        follower_count=follower_count,
        following_count=following_count,
        is_following=is_following,
    )


@app.route("/edit_profile", methods=["GET", "POST"])
@login_required
def edit_profile():
    conn = get_db_connection()

    if request.method == "POST":
        major = request.form.get("major", "").strip()
        instrument = request.form.get("instrument", "").strip()
        favorite_genre = request.form.get("favorite_genre", "").strip()
        bio = request.form.get("bio", "").strip()

        current_user = conn.execute("""
            SELECT *
            FROM users
            WHERE id = ?
        """, (session["user_id"],)).fetchone()

        profile_image_name = current_user["profile_image"]
        profile_image_file = request.files.get("profile_image")

        if profile_image_file and profile_image_file.filename:
            saved_name = save_uploaded_file(profile_image_file)
            if saved_name is None:
                conn.close()
                flash("That profile image file type is not allowed.")
                return redirect(url_for("edit_profile"))
            profile_image_name = saved_name

        conn.execute("""
            UPDATE users
            SET major = ?, instrument = ?, favorite_genre = ?, bio = ?, profile_image = ?
            WHERE id = ?
        """, (
            major,
            instrument,
            favorite_genre,
            bio,
            profile_image_name,
            session["user_id"]
        ))
        conn.commit()
        conn.close()

        flash("Profile updated.")
        return redirect(url_for("profile", username=session["username"]))

    user = conn.execute("""
        SELECT *
        FROM users
        WHERE id = ?
    """, (session["user_id"],)).fetchone()
    conn.close()

    return render_template("edit_profile.html", user=user)


@app.route("/followers/<username>")
@login_required
def followers_list(username):
    conn = get_db_connection()

    profile_user = conn.execute("""
        SELECT *
        FROM users
        WHERE username = ?
    """, (username,)).fetchone()

    if not profile_user:
        conn.close()
        flash("User not found.")
        return redirect(url_for("home"))

    followers = conn.execute("""
        SELECT users.*
        FROM follows
        JOIN users ON follows.follower_id = users.id
        WHERE follows.following_id = ?
        ORDER BY users.username ASC
    """, (profile_user["id"],)).fetchall()

    conn.close()

    return render_template(
        "followers_list.html",
        profile_user=profile_user,
        users=followers
    )


@app.route("/following/<username>")
@login_required
def following_list(username):
    conn = get_db_connection()

    profile_user = conn.execute("""
        SELECT *
        FROM users
        WHERE username = ?
    """, (username,)).fetchone()

    if not profile_user:
        conn.close()
        flash("User not found.")
        return redirect(url_for("home"))

    following = conn.execute("""
        SELECT users.*
        FROM follows
        JOIN users ON follows.following_id = users.id
        WHERE follows.follower_id = ?
        ORDER BY users.username ASC
    """, (profile_user["id"],)).fetchall()

    conn.close()

    return render_template(
        "following_list.html",
        profile_user=profile_user,
        users=following
    )


@app.route("/follow/<int:user_id>", methods=["POST"])
@login_required
def follow_user(user_id):
    if user_id == session["user_id"]:
        flash("You cannot follow yourself.")
        return redirect(request.referrer or url_for("home"))

    conn = get_db_connection()
    try:
        conn.execute("""
            INSERT INTO follows (follower_id, following_id)
            VALUES (?, ?)
        """, (session["user_id"], user_id))
        conn.commit()
    except sqlite3.IntegrityError:
        pass
    finally:
        conn.close()

    return redirect(request.referrer or url_for("home"))


@app.route("/unfollow/<int:user_id>", methods=["POST"])
@login_required
def unfollow_user(user_id):
    conn = get_db_connection()
    conn.execute("""
        DELETE FROM follows
        WHERE follower_id = ? AND following_id = ?
    """, (session["user_id"], user_id))
    conn.commit()
    conn.close()

    return redirect(request.referrer or url_for("home"))


# -------------------------
# SEARCH ROUTE
# -------------------------

@app.route("/search")
@login_required
def search():
    query = request.args.get("q", "").strip()

    conn = get_db_connection()

    if query:
        users = conn.execute("""
            SELECT *
            FROM users
            WHERE username LIKE ?
               OR instrument LIKE ?
               OR major LIKE ?
               OR favorite_genre LIKE ?
            ORDER BY username ASC
        """, (
            f"%{query}%",
            f"%{query}%",
            f"%{query}%",
            f"%{query}%"
        )).fetchall()
    else:
        users = []

    conn.close()

    return render_template("search.html", users=users, query=query)


# -------------------------
# GROUP ROUTES
# -------------------------

@app.route("/groups")
@login_required
def groups():
    conn = get_db_connection()

    groups = conn.execute("""
        SELECT groups.*, COUNT(group_members.id) AS member_count
        FROM groups
        LEFT JOIN group_members ON groups.id = group_members.group_id
        GROUP BY groups.id
    """).fetchall()

    conn.close()
    return render_template("groups.html", groups=groups)


@app.route("/groups/create", methods=["GET", "POST"])
@login_required
def create_group():
    if request.method == "POST":
        name = request.form.get("name")
        description = request.form.get("description")

        if not name or not description:
            flash("Fill everything out")
            return redirect(url_for("create_group"))

        conn = get_db_connection()

        cursor = conn.execute(
            "INSERT INTO groups (name, description, creator_id) VALUES (?, ?, ?)",
            (name, description, session["user_id"])
        )

        group_id = cursor.lastrowid

        conn.execute(
            "INSERT INTO group_members (group_id, user_id) VALUES (?, ?)",
            (group_id, session["user_id"])
        )

        conn.commit()
        conn.close()

        return redirect(url_for("group_chat", group_id=group_id))

    return render_template("create_group.html")


@app.route("/join_group/<int:group_id>", methods=["POST"])
def join_group(group_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    user_id = session["user_id"]

    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()

    # make sure table exists
    c.execute("""
    CREATE TABLE IF NOT EXISTS group_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        group_id INTEGER
    )
    """)

    # prevent duplicate join
    c.execute("""
        SELECT * FROM group_members WHERE user_id=? AND group_id=?
    """, (user_id, group_id))

    if not c.fetchone():
        c.execute("""
            INSERT INTO group_members (user_id, group_id)
            VALUES (?, ?)
        """, (user_id, group_id))

    conn.commit()
    conn.close()

    # ✅ THIS IS THE IMPORTANT FIX
    return redirect(url_for("group_chat", group_id=group_id))

@app.route("/group_chat/<int:group_id>")
def group_chat(group_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()

    c.execute("SELECT * FROM groups WHERE id=?", (group_id,))
    group = c.fetchone()

    conn.close()

    return render_template("group_chat.html", group=group)

@app.route("/groups/<int:group_id>", methods=["GET", "POST"])
@login_required
def group_chat(group_id):
    conn = get_db_connection()

    group = conn.execute(
        "SELECT * FROM groups WHERE id = ?",
        (group_id,)
    ).fetchone()

    membership = conn.execute(
        "SELECT * FROM group_members WHERE group_id = ? AND user_id = ?",
        (group_id, session["user_id"])
    ).fetchone()

    if not membership:
        conn.close()
        flash("Join the group first")
        return redirect(url_for("groups"))

    if request.method == "POST":
        content = request.form.get("content", "").strip()

        if not content:
            conn.close()
            return redirect(url_for("group_chat", group_id=group_id))

        conn.execute(
            "INSERT INTO messages (sender_id, group_id, content) VALUES (?, ?, ?)",
            (session["user_id"], group_id, content)
        )

        conn.commit()

        return redirect(url_for("group_chat", group_id=group_id))

    messages = conn.execute("""
        SELECT messages.*, users.username
        FROM messages
        JOIN users ON messages.sender_id = users.id
        WHERE group_id = ?
        ORDER BY messages.id ASC
    """, (group_id,)).fetchall()

    conn.close()

    return render_template(
        "group_chat.html",
        group=group,
        messages=messages
    )
@app.route("/delete_comment/<int:comment_id>", methods=["POST"])
def delete_comment(comment_id):
    if "user_id" not in session:
        return redirect(url_for("login"))

    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()

    c.execute("""
        DELETE FROM comments
        WHERE id=? AND user_id=?
    """, (comment_id, session["user_id"]))

    conn.commit()
    conn.close()

    return redirect(request.referrer or url_for("home"))

    @app.route("/search_groups")
def search_groups():
    query = request.args.get("q", "")

    conn = sqlite3.connect(DATABASE)
    c = conn.cursor()

    c.execute("""
        SELECT * FROM groups
        WHERE name LIKE ?
    """, ('%' + query + '%',))

    groups = c.fetchall()
    conn.close()

    return render_template("groups.html", groups=groups)
# -------------------------
# PRIVATE MESSAGE ROUTES
#------------------------

@app.route("/messages/<int:user_id>", methods=["GET", "POST"])
@login_required
def private_messages(user_id):
    conn = get_db_connection()
    current_user_id = session.get("user_id")

    # Get the user you're chatting with
    other_user = conn.execute(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    ).fetchone()

    if not other_user:
        conn.close()
        flash("User not found")
        return redirect(url_for("home"))

    # Prevent messaging yourself
    if current_user_id == user_id:
        conn.close()
        flash("You can't message yourself")
        return redirect(url_for("home"))

    # SEND MESSAGE
    if request.method == "POST":
        content = request.form.get("content", "").strip()

        if not content:
            conn.close()
            flash("Message cannot be empty")
            return redirect(url_for("private_messages", user_id=user_id))

        conn.execute(
            "INSERT INTO messages (sender_id, receiver_id, content) VALUES (?, ?, ?)",
            (current_user_id, user_id, content)
        )
        conn.commit()

        conn.close()
        return redirect(url_for("private_messages", user_id=user_id))

    # LOAD CONVERSATION
    messages = conn.execute("""
        SELECT messages.*, users.username
        FROM messages
        JOIN users ON messages.sender_id = users.id
        WHERE (messages.sender_id = ? AND messages.receiver_id = ?)
           OR (messages.sender_id = ? AND messages.receiver_id = ?)
        ORDER BY messages.id ASC
    """, (current_user_id, user_id, user_id, current_user_id)).fetchall()

    conn.close()

    return render_template(
        "private_messages.html",
        messages=messages,
        other_user=other_user
    )

@app.route("/messages/username/<username>")
@login_required
def private_messages_by_username(username):
    conn = get_db_connection()

    user = conn.execute(
        "SELECT * FROM users WHERE username = ?",
        (username,)
    ).fetchone()

    conn.close()

    if not user:
        flash("User not found")
        return redirect(url_for("home"))

    return redirect(url_for("private_messages", user_id=user["id"]))

# -------------------------
# INBOX ROUTE
# -------------------------

@app.route("/inbox")
@login_required
def inbox():
    conn = get_db_connection()
    current_user_id = session["user_id"]

    conversations = conn.execute("""
        SELECT DISTINCT users.id, users.username, users.profile_image
        FROM messages
        JOIN users
            ON users.id = CASE
                WHEN messages.sender_id = ? THEN messages.receiver_id
                ELSE messages.sender_id
            END
        WHERE messages.receiver_id IS NOT NULL
          AND (messages.sender_id = ? OR messages.receiver_id = ?)
        ORDER BY users.username ASC
    """, (current_user_id, current_user_id, current_user_id)).fetchall()

    conn.close()

    return render_template("inbox.html", conversations=conversations)


# -------------------------
# SOCKETS
# -------------------------

@socketio.on("join_group_room")
def handle_join_group_room(data):
    group_id = data.get("group_id")

    if group_id:
        join_room(f"group_{group_id}")


# -------------------------
# STARTUP
# -------------------------

init_db()

if __name__ == "__main__":
    socketio.run(app, debug=True)