import os
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

import click
from flask import (
    Flask,
    abort,
    current_app,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename


ROOT = Path(__file__).resolve().parent
DATABASE = ROOT / "instance" / "emporium.sqlite3"
UPLOAD_FOLDER = ROOT / "static" / "uploads"
PORTFOLIO_CATEGORIES = ("Characters", "Worlds", "Creatures", "Props")
VAULT_CATEGORIES = ("Character", "Environment", "Creature", "Prop", "Material")
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
MAX_UPLOAD_BYTES = 100 * 1024 * 1024
MAX_ADMIN_PASSWORD_LENGTH = 1024
DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))

SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL UNIQUE,
    username TEXT,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer' CHECK (role IN ('viewer', 'admin')),
    email_verified INTEGER NOT NULL DEFAULT 0,
    verification_token_hash TEXT,
    verification_expires_at INTEGER,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS portfolio_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    thumbnail TEXT NOT NULL,
    download_file TEXT,
    download_original_filename TEXT,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS vault_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    category TEXT NOT NULL,
    description TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT '',
    filename TEXT NOT NULL,
    original_filename TEXT NOT NULL,
    preview TEXT,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (
    ip_address TEXT PRIMARY KEY,
    failures INTEGER NOT NULL DEFAULT 0,
    locked_until INTEGER NOT NULL DEFAULT 0
);
"""


def create_app(test_config=None):
    app = Flask(__name__, static_folder=None)
    app.config.update(
        SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
        MAX_CONTENT_LENGTH=MAX_UPLOAD_BYTES,
        DATABASE=str(DATABASE),
        UPLOAD_FOLDER=str(UPLOAD_FOLDER),
        ADMIN_EMAIL=os.environ.get("ADMIN_EMAIL", "alqaqa469@gmail.com").strip().lower(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "0") == "1",
        PERMANENT_SESSION_LIFETIME=60 * 60 * 8,
    )
    if test_config:
        app.config.update(test_config)
    Path(app.config["DATABASE"]).parent.mkdir(parents=True, exist_ok=True)
    Path(app.config["UPLOAD_FOLDER"]).mkdir(parents=True, exist_ok=True)

    @contextmanager
    def connect_db():
        connection = sqlite3.connect(app.config["DATABASE"], timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    with connect_db() as connection:
        connection.executescript(SCHEMA)
        account_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(accounts)")
        }
        if "username" not in account_columns:
            connection.execute("ALTER TABLE accounts ADD COLUMN username TEXT")
        portfolio_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(portfolio_items)")
        }
        if "download_file" not in portfolio_columns:
            connection.execute("ALTER TABLE portfolio_items ADD COLUMN download_file TEXT")
        if "download_original_filename" not in portfolio_columns:
            connection.execute(
                "ALTER TABLE portfolio_items ADD COLUMN download_original_filename TEXT"
            )
        connection.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS accounts_username_unique "
            "ON accounts(username) WHERE username IS NOT NULL"
        )

    @app.before_request
    def load_current_user():
        g.current_user = None
        account_id = session.get("account_id")
        if account_id:
            with connect_db() as connection:
                g.current_user = connection.execute(
                    "SELECT id, email, role, email_verified FROM accounts WHERE id = ?",
                    (account_id,),
                ).fetchone()
            if not g.current_user:
                session.clear()

    @app.before_request
    def protect_post_requests():
        if request.method == "POST":
            token = session.get("_csrf_token", "")
            submitted = request.form.get("_csrf_token", "")
            if not token or not secrets.compare_digest(token, submitted):
                abort(400, "The form expired. Please reload and try again.")

    @app.after_request
    def add_security_headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; style-src 'self'; "
            "script-src 'self'; object-src 'none'; base-uri 'self'; "
            "frame-ancestors 'none'; form-action 'self'"
        )
        return response

    @app.context_processor
    def inject_template_globals():
        if "_csrf_token" not in session:
            session["_csrf_token"] = secrets.token_urlsafe(32)
        return {
            "csrf_token": session["_csrf_token"],
            "current_user": g.get("current_user"),
        }

    @app.template_filter("datetimeformat")
    def format_date(timestamp):
        return datetime.fromtimestamp(int(timestamp), timezone.utc).strftime("%b %Y")

    @app.get("/static/<path:filename>", endpoint="static")
    def static_files(filename):
        if filename == "uploads" or filename.startswith("uploads/"):
            abort(404)
        return send_from_directory(ROOT / "static", filename)

    @app.get("/media/<path:filename>")
    def media(filename):
        if not g.current_user:
            abort(403)
        safe_name = Path(filename).name
        if (
            "/" in filename
            or "\\" in filename
            or safe_name != filename
            or safe_name.rsplit(".", 1)[-1].lower() not in IMAGE_EXTENSIONS
        ):
            abort(404)
        return send_from_directory(
            app.config["UPLOAD_FOLDER"], safe_name, mimetype=_image_mimetype(safe_name)
        )

    @app.get("/")
    def index():
        if not g.current_user:
            return render_template("login.html")
        with connect_db() as connection:
            portfolio = connection.execute(
                "SELECT * FROM portfolio_items ORDER BY created_at DESC, id DESC"
            ).fetchall()
            assets = connection.execute(
                "SELECT * FROM vault_assets ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return render_template(
            "index.html",
            portfolio=portfolio,
            assets=assets,
            portfolio_categories=PORTFOLIO_CATEGORIES,
        )

    @app.get("/admin/login")
    def admin_login():
        return redirect(url_for("index"))

    @app.post("/admin/login")
    def admin_login_submit():
        ip_address = request.remote_addr or "unknown"
        now = int(time.time())
        with connect_db() as connection:
            attempt = connection.execute(
                "SELECT failures, locked_until FROM login_attempts WHERE ip_address = ?",
                (ip_address,),
            ).fetchone()
            if attempt and attempt["locked_until"] > now:
                flash("Too many attempts. Please wait a few minutes before trying again.", "error")
                return redirect(url_for("admin_login"))
            account = connection.execute(
                "SELECT id, password_hash, role FROM accounts "
                "WHERE lower(email) = ? OR lower(username) = ? LIMIT 1",
                (
                    request.form.get("username", "").strip().lower(),
                    request.form.get("username", "").strip().lower(),
                ),
            ).fetchone()
            password = request.form.get("password", "")
            password_hash = account["password_hash"] if account else DUMMY_PASSWORD_HASH
            bounded_password = password if len(password) <= MAX_ADMIN_PASSWORD_LENGTH else ""
            if (
                check_password_hash(password_hash, bounded_password)
                and account
                and bounded_password == password
            ):
                connection.execute("DELETE FROM login_attempts WHERE ip_address = ?", (ip_address,))
                session.clear()
                session["account_id"] = account["id"]
                session.permanent = True
                return redirect(url_for("index"))
            previous_failures = 0 if attempt and attempt["locked_until"] else (
                attempt["failures"] if attempt else 0
            )
            failures = previous_failures + 1
            locked_until = now + 300 if failures >= 5 else 0
            connection.execute(
                "INSERT INTO login_attempts (ip_address, failures, locked_until) VALUES (?, ?, ?) "
                "ON CONFLICT(ip_address) DO UPDATE SET failures = excluded.failures, "
                "locked_until = excluded.locked_until",
                (ip_address, failures, locked_until),
            )
        flash("Those username/email and password details did not match.", "error")
        return redirect(url_for("admin_login"))

    @app.post("/register")
    def register():
        username = request.form.get("username", "").strip().lower()
        password = request.form.get("password", "")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,29}", username):
            flash("Choose a username with 3–30 letters, numbers, dots, dashes, or underscores.", "error")
            return redirect(url_for("index"))
        if username == app.config["ADMIN_EMAIL"]:
            flash("That username is reserved.", "error")
            return redirect(url_for("index"))
        if len(password) < 8 or len(password) > MAX_ADMIN_PASSWORD_LENGTH:
            flash("Passwords must be at least 8 characters long.", "error")
            return redirect(url_for("index"))
        with connect_db() as connection:
            existing = connection.execute(
                "SELECT id, email_verified FROM accounts "
                "WHERE lower(username) = ? OR lower(email) = ? LIMIT 1",
                (username, username),
            ).fetchone()
            if existing and existing["email_verified"]:
                flash("That username is already taken. Please sign in or choose another.", "error")
                return redirect(url_for("index"))
            if existing:
                connection.execute(
                    "UPDATE accounts SET username = ?, email = ?, password_hash = ?, email_verified = 1, "
                    "verification_token_hash = NULL, verification_expires_at = NULL "
                    "WHERE id = ?",
                    (username, username, generate_password_hash(password), existing["id"]),
                )
            else:
                connection.execute(
                    "INSERT INTO accounts "
                    "(email, username, password_hash, role, email_verified, created_at) "
                    "VALUES (?, ?, ?, 'viewer', 1, ?)",
                    (username, username, generate_password_hash(password), int(time.time())),
                )
        flash("Account created. You can now sign in with your username and password.", "success")
        return redirect(url_for("index"))

    def viewer_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not g.current_user:
                return redirect(url_for("index"))
            return view(*args, **kwargs)

        return wrapped

    def admin_required(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not g.current_user:
                return redirect(url_for("index"))
            if g.current_user["role"] != "admin":
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    @app.get("/admin")
    @admin_required
    def admin_dashboard():
        with connect_db() as connection:
            portfolio = connection.execute(
                "SELECT * FROM portfolio_items ORDER BY created_at DESC, id DESC"
            ).fetchall()
            assets = connection.execute(
                "SELECT * FROM vault_assets ORDER BY created_at DESC, id DESC"
            ).fetchall()
        return render_template(
            "admin.html",
            portfolio=portfolio,
            assets=assets,
            portfolio_categories=PORTFOLIO_CATEGORIES,
            vault_categories=VAULT_CATEGORIES,
        )

    @app.post("/admin/logout")
    @admin_required
    def admin_logout():
        session.clear()
        flash("You have been signed out.", "success")
        return redirect(url_for("index"))

    @app.post("/logout")
    @viewer_required
    def logout():
        session.clear()
        flash("You have been signed out.", "success")
        return redirect(url_for("index"))

    @app.post("/admin/portfolio")
    @admin_required
    def add_portfolio_item():
        title = _required_text("title", "Title", 100)
        category = request.form.get("category", "")
        description = _required_text("description", "Description", 1200)
        tags = _tags(request.form.get("tags", ""))
        image = request.files.get("thumbnail")
        if category not in PORTFOLIO_CATEGORIES:
            abort(400, "Choose a valid portfolio category.")
        download = request.files.get("download_file")
        if download and download.filename:
            _validated_upload(download, None, "Choose a downloadable file.")
        thumbnail = _save_upload(image, IMAGE_EXTENSIONS, "Choose a preview image.")
        download_file = None
        download_original_filename = None
        if download and download.filename:
            download_file = _save_upload(download, None, "Choose a downloadable file.")
            download_original_filename = secure_filename(download.filename)
        with connect_db() as connection:
            connection.execute(
                "INSERT INTO portfolio_items "
                "(title, category, description, tags, thumbnail, download_file, "
                "download_original_filename, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    title,
                    category,
                    description,
                    tags,
                    thumbnail,
                    download_file,
                    download_original_filename,
                    int(time.time()),
                ),
            )
        flash("Portfolio piece added.", "success")
        return redirect(url_for("admin_dashboard") + "#portfolio")

    @app.post("/admin/vault")
    @admin_required
    def add_vault_asset():
        title = _required_text("title", "Title", 100)
        category = request.form.get("category", "")
        description = _required_text("description", "Description", 1200)
        tags = _tags(request.form.get("tags", ""))
        if category not in VAULT_CATEGORIES:
            abort(400, "Choose a valid vault category.")
        asset_file = request.files.get("asset")
        preview_file = request.files.get("preview")
        if preview_file and preview_file.filename:
            _validated_upload(preview_file, IMAGE_EXTENSIONS, "Preview must be a JPG, PNG, or WebP image.")
        filename = _save_upload(asset_file, None, "Choose a file to upload.")
        original_filename = secure_filename(asset_file.filename)
        preview = None
        if preview_file and preview_file.filename:
            preview = _save_upload(preview_file, IMAGE_EXTENSIONS, "Preview must be a JPG, PNG, or WebP image.")
        with connect_db() as connection:
            connection.execute(
                "INSERT INTO vault_assets "
                "(title, category, description, tags, filename, original_filename, preview, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    title,
                    category,
                    description,
                    tags,
                    filename,
                    original_filename,
                    preview,
                    int(time.time()),
                ),
            )
        flash("Free asset added to the vault.", "success")
        return redirect(url_for("admin_dashboard") + "#vault")

    @app.post("/admin/portfolio/<int:item_id>/edit")
    @admin_required
    def edit_portfolio_item(item_id):
        with connect_db() as connection:
            item = connection.execute(
                "SELECT * FROM portfolio_items WHERE id = ?", (item_id,)
            ).fetchone()
        if not item:
            abort(404)
        title = _required_text("title", "Title", 100)
        category = request.form.get("category", "")
        description = _required_text("description", "Description", 1200)
        tags = _tags(request.form.get("tags", ""))
        if category not in PORTFOLIO_CATEGORIES:
            abort(400, "Choose a valid portfolio category.")
        thumbnail = item["thumbnail"]
        image = request.files.get("thumbnail")
        replacement_thumbnail = None
        if image and image.filename:
            _validated_upload(image, IMAGE_EXTENSIONS, "Choose a preview image.")
            replacement_thumbnail = _save_upload(image, IMAGE_EXTENSIONS, "Choose a preview image.")
            thumbnail = replacement_thumbnail
        download_file = item["download_file"]
        download_original_filename = item["download_original_filename"]
        replacement_download = None
        downloadable = request.files.get("download_file")
        if downloadable and downloadable.filename:
            _validated_upload(downloadable, None, "Choose a downloadable file.")
            replacement_download = _save_upload(downloadable, None, "Choose a downloadable file.")
            download_file = replacement_download
            download_original_filename = secure_filename(downloadable.filename)
        remove_download = (
            request.form.get("remove_download") == "1" and not replacement_download
        )
        if remove_download:
            download_file = None
            download_original_filename = None
        with connect_db() as connection:
            connection.execute(
                "UPDATE portfolio_items SET title = ?, category = ?, description = ?, tags = ?, "
                "thumbnail = ?, download_file = ?, download_original_filename = ? WHERE id = ?",
                (
                    title,
                    category,
                    description,
                    tags,
                    thumbnail,
                    download_file,
                    download_original_filename,
                    item_id,
                ),
            )
        if replacement_thumbnail:
            _remove_upload(item["thumbnail"])
        if replacement_download or remove_download:
            _remove_upload(item["download_file"])
        flash("Portfolio piece updated.", "success")
        return redirect(url_for("admin_dashboard") + "#portfolio")

    @app.post("/admin/portfolio/<int:item_id>/delete")
    @admin_required
    def delete_portfolio_item(item_id):
        with connect_db() as connection:
            item = connection.execute(
                "SELECT thumbnail, download_file FROM portfolio_items WHERE id = ?", (item_id,)
            ).fetchone()
            if item:
                connection.execute("DELETE FROM portfolio_items WHERE id = ?", (item_id,))
        if item:
            _remove_upload(item["thumbnail"])
            _remove_upload(item["download_file"])
            flash("Portfolio piece removed.", "success")
        else:
            flash("That portfolio piece no longer exists.", "error")
        return redirect(url_for("admin_dashboard") + "#portfolio")

    @app.post("/admin/vault/<int:asset_id>/edit")
    @admin_required
    def edit_vault_asset(asset_id):
        with connect_db() as connection:
            asset = connection.execute(
                "SELECT * FROM vault_assets WHERE id = ?", (asset_id,)
            ).fetchone()
        if not asset:
            abort(404)
        title = _required_text("title", "Title", 100)
        category = request.form.get("category", "")
        description = _required_text("description", "Description", 1200)
        tags = _tags(request.form.get("tags", ""))
        if category not in VAULT_CATEGORIES:
            abort(400, "Choose a valid vault category.")
        filename = asset["filename"]
        original_filename = asset["original_filename"]
        replacement_filename = None
        asset_file = request.files.get("asset")
        if asset_file and asset_file.filename:
            _validated_upload(asset_file, None, "Choose a file to upload.")
        preview_file = request.files.get("preview")
        if preview_file and preview_file.filename:
            _validated_upload(
                preview_file, IMAGE_EXTENSIONS, "Preview must be a JPG, PNG, or WebP image."
            )
        if asset_file and asset_file.filename:
            replacement_filename = _save_upload(
                asset_file, None, "Choose a file to upload."
            )
            filename = replacement_filename
            original_filename = secure_filename(asset_file.filename)
        preview = asset["preview"]
        replacement_preview = None
        if preview_file and preview_file.filename:
            replacement_preview = _save_upload(
                preview_file,
                IMAGE_EXTENSIONS,
                "Preview must be a JPG, PNG, or WebP image.",
            )
            preview = replacement_preview
        with connect_db() as connection:
            connection.execute(
                "UPDATE vault_assets SET title = ?, category = ?, description = ?, tags = ?, filename = ?, original_filename = ?, preview = ? WHERE id = ?",
                (title, category, description, tags, filename, original_filename, preview, asset_id),
            )
        if replacement_filename:
            _remove_upload(asset["filename"])
        if replacement_preview:
            _remove_upload(asset["preview"])
        flash("Asset updated in the vault.", "success")
        return redirect(url_for("admin_dashboard") + "#vault")

    @app.post("/admin/vault/<int:asset_id>/delete")
    @admin_required
    def delete_vault_asset(asset_id):
        with connect_db() as connection:
            asset = connection.execute(
                "SELECT filename, preview FROM vault_assets WHERE id = ?", (asset_id,)
            ).fetchone()
            if asset:
                connection.execute("DELETE FROM vault_assets WHERE id = ?", (asset_id,))
        if asset:
            _remove_upload(asset["filename"])
            _remove_upload(asset["preview"])
            flash("Asset removed from the vault.", "success")
        else:
            flash("That asset no longer exists.", "error")
        return redirect(url_for("admin_dashboard") + "#vault")

    @app.get("/download/<int:asset_id>")
    @viewer_required
    def download_asset(asset_id):
        with connect_db() as connection:
            asset = connection.execute(
                "SELECT filename, original_filename FROM vault_assets WHERE id = ?", (asset_id,)
            ).fetchone()
        if not asset:
            abort(404)
        return send_from_directory(
            app.config["UPLOAD_FOLDER"],
            asset["filename"],
            as_attachment=True,
            download_name=asset["original_filename"],
            max_age=0,
        )

    @app.get("/portfolio-download/<int:item_id>")
    @viewer_required
    def download_portfolio_file(item_id):
        with connect_db() as connection:
            item = connection.execute(
                "SELECT download_file, download_original_filename FROM portfolio_items WHERE id = ?",
                (item_id,),
            ).fetchone()
        if not item or not item["download_file"]:
            abort(404)
        return send_from_directory(
            app.config["UPLOAD_FOLDER"],
            item["download_file"],
            as_attachment=True,
            download_name=item["download_original_filename"],
            max_age=0,
        )

    @app.cli.command("create-admin")
    @click.argument("email")
    def create_admin(email):
        """Create the studio administrator with a prompted password."""
        email = email.strip().lower()
        if email != app.config["ADMIN_EMAIL"]:
            raise click.ClickException(f"Admin email must match ADMIN_EMAIL ({app.config['ADMIN_EMAIL']}).")
        with connect_db() as connection:
            if connection.execute("SELECT 1 FROM accounts WHERE role = 'admin' LIMIT 1").fetchone():
                raise click.ClickException("An administrator account already exists.")
        password = click.prompt("Admin password", hide_input=True, confirmation_prompt=True)
        if not 8 <= len(password) <= MAX_ADMIN_PASSWORD_LENGTH:
            raise click.ClickException("Password must be between 8 and 1024 characters.")
        with connect_db() as connection:
            connection.execute(
                "INSERT INTO accounts (email, password_hash, role, email_verified, created_at) "
                "VALUES (?, ?, 'admin', 1, ?)",
                (email, generate_password_hash(password), int(time.time())),
            )
        click.echo("Administrator created. Sign in at /admin/login.")

    @app.errorhandler(413)
    def upload_too_large(_error):
        return "Upload is too large. The maximum request size is 100 MB.", 413

    return app


def _required_text(field, label, maximum):
    value = request.form.get(field, "").strip()
    if not value or len(value) > maximum:
        abort(400, f"{label} is required and must be at most {maximum} characters.")
    return value


def _tags(value):
    return ", ".join(part.strip()[:32] for part in value.split(",") if part.strip())[:400]


def _save_upload(upload, allowed_extensions, missing_message):
    original, extension = _validated_upload(upload, allowed_extensions, missing_message)
    suffix = f".{extension}" if extension and len(extension) <= 16 else ""
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    upload.save(Path(current_app.config["UPLOAD_FOLDER"]) / stored_name)
    return stored_name


def _validated_upload(upload, allowed_extensions, missing_message):
    if not upload or not upload.filename:
        abort(400, missing_message)
    original = secure_filename(upload.filename)
    if not original or len(original) > 255:
        abort(400, "That file name is not valid.")
    extension = original.rsplit(".", 1)[1].lower() if "." in original else ""
    if allowed_extensions is not None and extension not in allowed_extensions:
        abort(400, f"Unsupported file type: .{extension}")
    if allowed_extensions == IMAGE_EXTENSIONS and extension in IMAGE_EXTENSIONS:
        header = upload.stream.read(12)
        upload.stream.seek(0)
        valid_header = (
            (extension in {"jpg", "jpeg"} and header.startswith(b"\xff\xd8\xff"))
            or (extension == "png" and header.startswith(b"\x89PNG\r\n\x1a\n"))
            or (
                extension == "webp"
                and header.startswith(b"RIFF")
                and header[8:12] == b"WEBP"
            )
        )
        if not valid_header:
            abort(400, "The preview file does not match its image type.")
    return original, extension


def _remove_upload(filename):
    if filename:
        path = Path(current_app.config["UPLOAD_FOLDER"]) / Path(filename).name
        if path.is_file():
            path.unlink()


def _image_mimetype(filename):
    extension = filename.rsplit(".", 1)[-1].lower()
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "webp": "image/webp",
    }[extension]


app = create_app()


if __name__ == "__main__":
    app.run(debug=os.environ.get("FLASK_DEBUG") == "1")
