"""
Project / Customer Tracker
Run with: python app.py
"""
import os
import sqlite3
from datetime import datetime
from functools import wraps

from flask import (
    Flask, g, render_template, request, redirect, url_for,
    flash, abort, jsonify
)
from flask_login import (
    LoginManager, UserMixin, login_user, logout_user,
    login_required, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
BASE_DIR = os.path.abspath(os.path.dirname(__file__))
# Default to a database.db file next to the source. Override with the
# DATABASE_PATH env var to point at e.g. a mounted persistent disk on
# Render: DATABASE_PATH=/var/data/database.db
DB_PATH = os.environ.get("DATABASE_PATH") or os.path.join(BASE_DIR, "database.db")

app = Flask(__name__)
# CHANGE THIS in production. Used to sign session cookies.
app.config["SECRET_KEY"] = os.environ.get(
    "TRACKER_SECRET_KEY",
    "change-me-to-a-long-random-string-please"
)

login_manager = LoginManager(app)
login_manager.login_view = "login"
login_manager.login_message_category = "warning"

# Tracker types and their display names
TRACKER_TYPES = [
    ("dropship", "Dropship"),
    ("supply_only", "Supply Only"),
    ("build_your_brand", "Build Your Brand"),
    ("white_label", "White Label"),
]
TRACKER_LABELS = dict(TRACKER_TYPES)

# Action types: standard for all trackers + extras for dropship.
# "Other" is always available so users can record activity that doesn't
# fit the predefined list. "comment" is used internally for comment-only
# entries. Neither participates in stage progression.
STANDARD_ACTIONS = [
    ("initial_contact", "Initial Contact"),
    ("in_discussion", "In Discussion"),
    ("agreement_sent", "Agreement Sent"),
    ("agreement_signed", "Agreement Signed"),
]
DROPSHIP_EXTRA_ACTIONS = [
    ("csv_sent", "CSV Sent"),
    ("products_listed", "Products Listed"),
    ("first_order_placed", "First Order Placed"),
]
OTHER_ACTION = [("other", "Other")]
ACTION_LABELS = dict(STANDARD_ACTIONS + DROPSHIP_EXTRA_ACTIONS + OTHER_ACTION)

# Stage progression order, used to show the "current stage" of a tracker.
# Higher index = further along.
STAGE_ORDER = [
    "initial_contact",
    "in_discussion",
    "agreement_sent",
    "agreement_signed",
    "csv_sent",
    "products_listed",
    "first_order_placed",
]


def get_action_choices(tracker_type: str):
    """Available action types for a given tracker."""
    if tracker_type == "dropship":
        return STANDARD_ACTIONS + DROPSHIP_EXTRA_ACTIONS + OTHER_ACTION
    return STANDARD_ACTIONS + OTHER_ACTION


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class User(UserMixin):
    def __init__(self, row):
        self.id = row["id"]
        self.username = row["username"]
        self.is_admin = bool(row["is_admin"])


@login_manager.user_loader
def load_user(user_id):
    row = get_db().execute(
        "SELECT * FROM users WHERE id = ?", (user_id,)
    ).fetchone()
    return User(row) if row else None


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated or not current_user.is_admin:
            flash("Admin access required.", "danger")
            return redirect(url_for("dashboard"))
        return view(*args, **kwargs)
    return wrapped


# ---------------------------------------------------------------------------
# Template helpers
# ---------------------------------------------------------------------------
@app.template_filter("nice_dt")
def nice_dt(value):
    """Render an ISO timestamp from SQLite as e.g. '29 Apr 2026, 18:57'."""
    return _format_dt(value)


@app.template_filter("tag_color")
def tag_color(name: str) -> str:
    """Return a CSS class name for a tag, deterministic per tag name.

    We use 12 colour palettes defined in style.css. Hashing on the name
    means the same tag always renders in the same colour regardless of
    its insertion order, and on every page that displays it.
    """
    if not name:
        return "tag-color-0"
    # md5 is overkill for this but it's stable across Python runs (unlike
    # the built-in hash() which is randomised) and trivially cheap here.
    import hashlib
    h = int(hashlib.md5(name.encode("utf-8")).hexdigest(), 16)
    return f"tag-color-{h % 12}"


def _format_dt(value):
    """Single source of truth for the display format used everywhere."""
    if not value:
        return ""
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return value
    # Day without leading zero on Linux uses "%-d", but that flag is
    # not portable to Windows. Strip the leading zero manually so this
    # works the same way everywhere (e.g. "5 Apr" not "05 Apr").
    return dt.strftime("%d %b %Y, %H:%M").lstrip("0")


@app.context_processor
def inject_globals():
    return {
        "TRACKER_TYPES": TRACKER_TYPES,
        "ACTION_LABELS": ACTION_LABELS,
    }


# ---------------------------------------------------------------------------
# Routes: Auth
# ---------------------------------------------------------------------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        row = get_db().execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()
        if row and check_password_hash(row["password_hash"], password):
            login_user(User(row))
            next_url = request.args.get("next") or url_for("dashboard")
            return redirect(next_url)
        flash("Invalid username or password.", "danger")
    return render_template("login.html")


@app.route("/logout")
@login_required
def logout():
    logout_user()
    flash("You have been logged out.", "info")
    return redirect(url_for("login"))


# ---------------------------------------------------------------------------
# Routes: Customers
# ---------------------------------------------------------------------------
@app.route("/")
@login_required
def dashboard():
    """Customer list. Initial filter is server-side; live filter is client-side JS."""
    db = get_db()
    q = request.args.get("q", "").strip()

    # Server-side search supports name, notes, and *all* contact fields.
    # Client-side JS does the live filter once the page is loaded.
    base_sql = """
        SELECT cu.*,
               (SELECT name  FROM contacts WHERE customer_id = cu.id ORDER BY id LIMIT 1) AS primary_contact_name,
               (SELECT email FROM contacts WHERE customer_id = cu.id ORDER BY id LIMIT 1) AS primary_contact_email,
               (SELECT COUNT(*) FROM contacts WHERE customer_id = cu.id)                  AS contact_count,
               (SELECT GROUP_CONCAT(
                          COALESCE(name, '')  || ' ' ||
                          COALESCE(role, '')  || ' ' ||
                          COALESCE(email, '') || ' ' ||
                          COALESCE(phone, ''),
                          ' | ')
                FROM contacts WHERE customer_id = cu.id)                                  AS all_contacts_text,
               (SELECT GROUP_CONCAT(t.name, '|')
                FROM customer_tags ct JOIN tags t ON t.id = ct.tag_id
                WHERE ct.customer_id = cu.id)                                             AS tags_csv
        FROM customers cu
    """
    if q:
        like = f"%{q}%"
        customers = db.execute(base_sql + """
            WHERE cu.name LIKE ?
               OR cu.notes LIKE ?
               OR EXISTS (
                    SELECT 1 FROM contacts co
                    WHERE co.customer_id = cu.id
                      AND (co.name LIKE ? OR co.email LIKE ? OR co.phone LIKE ? OR co.role LIKE ?)
               )
            ORDER BY datetime(cu.created_at) DESC, cu.id DESC
        """, (like, like, like, like, like, like)).fetchall()
    else:
        customers = db.execute(
            base_sql + " ORDER BY datetime(cu.created_at) DESC, cu.id DESC"
        ).fetchall()

    # For each customer, work out the "current stage" of each tracker:
    # the most-progressed action that has been logged.
    customer_stages = {}
    for cust in customers:
        stages = {}
        rows = db.execute("""
            SELECT t.tracker_type, a.action_type
            FROM trackers t
            LEFT JOIN actions a ON a.tracker_id = t.id
            WHERE t.customer_id = ?
        """, (cust["id"],)).fetchall()
        for r in rows:
            ttype = r["tracker_type"]
            atype = r["action_type"]
            if not atype or atype not in STAGE_ORDER:
                continue  # ignore comment-only entries when computing stage
            current = stages.get(ttype)
            if current is None or STAGE_ORDER.index(atype) > STAGE_ORDER.index(current):
                stages[ttype] = atype
        customer_stages[cust["id"]] = stages

    # All tags currently in use anywhere – needed for the filter dropdown.
    all_tags = [r["name"] for r in db.execute(
        "SELECT name FROM tags ORDER BY name COLLATE NOCASE"
    ).fetchall()]

    return render_template(
        "dashboard.html",
        customers=customers,
        customer_stages=customer_stages,
        all_tags=all_tags,
        q=q,
    )


@app.route("/customers/new", methods=["GET", "POST"])
@login_required
def new_customer():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Customer name is required.", "danger")
            return render_template("customer_form.html", customer=None,
                                   form=request.form, all_tags=_all_tag_names())

        db = get_db()
        cur = db.execute("""
            INSERT INTO customers (name, notes, created_by)
            VALUES (?, ?, ?)
        """, (
            name,
            request.form.get("notes", "").strip() or None,
            current_user.id,
        ))
        customer_id = cur.lastrowid

        # Auto-create the four trackers
        for ttype, _label in TRACKER_TYPES:
            db.execute(
                "INSERT INTO trackers (customer_id, tracker_type) VALUES (?, ?)",
                (customer_id, ttype),
            )

        # Optional initial contact ----------------------------------------
        # If a name is given, create one contact with whatever fields were
        # filled in. Subsequent contacts can be added on the customer page.
        c_name = request.form.get("contact_name", "").strip()
        if c_name:
            db.execute("""
                INSERT INTO contacts (customer_id, name, role, email, phone, notes, created_by)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                customer_id, c_name,
                request.form.get("contact_role", "").strip() or None,
                request.form.get("contact_email", "").strip() or None,
                request.form.get("contact_phone", "").strip() or None,
                request.form.get("contact_notes", "").strip() or None,
                current_user.id,
            ))

        # Optional tags --------------------------------------------------
        raw_tags = request.form.get("tags", "")
        for raw in raw_tags.split(","):
            tname = _normalise_tag(raw)
            if not tname:
                continue
            ins = db.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (tname,))
            if ins.rowcount:
                tag_id = ins.lastrowid
            else:
                tag_id = db.execute("SELECT id FROM tags WHERE name = ?", (tname,)).fetchone()["id"]
            db.execute(
                "INSERT OR IGNORE INTO customer_tags (customer_id, tag_id) VALUES (?, ?)",
                (customer_id, tag_id),
            )

        db.commit()
        flash(f"Customer '{name}' created.", "success")
        return redirect(url_for("view_customer", customer_id=customer_id))

    return render_template("customer_form.html", customer=None, form={},
                           all_tags=_all_tag_names())


def _all_tag_names():
    """Helper used by both customer_form and edit views for autocomplete."""
    return [r["name"] for r in get_db().execute(
        "SELECT name FROM tags ORDER BY name COLLATE NOCASE"
    ).fetchall()]


@app.route("/customers/<int:customer_id>")
@login_required
def view_customer(customer_id):
    db = get_db()
    customer = db.execute(
        "SELECT * FROM customers WHERE id = ?", (customer_id,)
    ).fetchone()
    if not customer:
        abort(404)

    # Make sure all four trackers exist (in case schema changed for old customers)
    existing = {
        r["tracker_type"]: r["id"]
        for r in db.execute(
            "SELECT id, tracker_type FROM trackers WHERE customer_id = ?",
            (customer_id,),
        ).fetchall()
    }
    for ttype, _ in TRACKER_TYPES:
        if ttype not in existing:
            cur = db.execute(
                "INSERT INTO trackers (customer_id, tracker_type) VALUES (?, ?)",
                (customer_id, ttype),
            )
            existing[ttype] = cur.lastrowid
    db.commit()

    # Build a dict of tracker_type -> {id, actions[], current_stage, products[]}
    trackers = {}
    for ttype, label in TRACKER_TYPES:
        tracker_id = existing[ttype]
        actions = db.execute("""
            SELECT a.*, u.username AS author
            FROM actions a
            LEFT JOIN users u ON u.id = a.created_by
            WHERE a.tracker_id = ?
            ORDER BY datetime(a.created_at) ASC, a.id ASC
        """, (tracker_id,)).fetchall()

        current_stage = None
        for a in actions:
            atype = a["action_type"]
            if atype not in STAGE_ORDER:
                continue
            if current_stage is None or STAGE_ORDER.index(atype) > STAGE_ORDER.index(current_stage):
                current_stage = atype

        products = []
        # The Products listed checklist is only displayed once the user
        # has actually marked "Products Listed" as an action — until then
        # it's noise on the customer page. Stays visible afterwards even
        # if later actions move the stage on.
        products_enabled = False
        if ttype == "dropship":
            products_enabled = any(a["action_type"] == "products_listed" for a in actions)
            if products_enabled:
                products = db.execute("""
                    SELECT p.id           AS product_id,
                           p.name         AS name,
                           COALESCE(tp.is_listed, 0) AS is_listed,
                           tp.updated_at  AS updated_at,
                           u.username     AS updated_by_name
                    FROM products p
                    LEFT JOIN tracker_products tp
                        ON tp.product_id = p.id AND tp.tracker_id = ?
                    LEFT JOIN users u ON u.id = tp.updated_by
                    WHERE p.is_active = 1
                    ORDER BY p.name COLLATE NOCASE
                """, (tracker_id,)).fetchall()

        trackers[ttype] = {
            "id": tracker_id,
            "label": label,
            "actions": actions,
            "current_stage": current_stage,
            "action_choices": get_action_choices(ttype),
            "products": products,
            "products_enabled": products_enabled,
        }

    return render_template(
        "customer.html",
        customer=customer,
        trackers=trackers,
        contacts=db.execute("""
            SELECT co.*, u.username AS created_by_name
            FROM contacts co
            LEFT JOIN users u ON u.id = co.created_by
            WHERE co.customer_id = ?
            ORDER BY co.id
        """, (customer_id,)).fetchall(),
        tags=db.execute("""
            SELECT t.id, t.name
            FROM tags t JOIN customer_tags ct ON ct.tag_id = t.id
            WHERE ct.customer_id = ?
            ORDER BY t.name
        """, (customer_id,)).fetchall(),
        all_tags=[r["name"] for r in db.execute(
            "SELECT name FROM tags ORDER BY name COLLATE NOCASE"
        ).fetchall()],
    )


@app.route("/customers/<int:customer_id>/edit", methods=["GET", "POST"])
@login_required
def edit_customer(customer_id):
    db = get_db()
    customer = db.execute(
        "SELECT * FROM customers WHERE id = ?", (customer_id,)
    ).fetchone()
    if not customer:
        abort(404)

    if request.method == "POST":
        name = request.form.get("name", "").strip()
        if not name:
            flash("Customer name is required.", "danger")
            return render_template("customer_form.html", customer=customer, form=request.form)

        db.execute("""
            UPDATE customers
               SET name = ?, notes = ?
             WHERE id = ?
        """, (
            name,
            request.form.get("notes", "").strip() or None,
            customer_id,
        ))
        db.commit()
        flash("Customer updated.", "success")
        return redirect(url_for("view_customer", customer_id=customer_id))

    return render_template("customer_form.html", customer=customer, form=customer)


@app.route("/customers/<int:customer_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_customer(customer_id):
    db = get_db()
    db.execute("DELETE FROM customers WHERE id = ?", (customer_id,))
    db.commit()
    flash("Customer deleted.", "info")
    return redirect(url_for("dashboard"))


# ---------------------------------------------------------------------------
# Routes: Contacts (multiple per customer)
# ---------------------------------------------------------------------------
@app.route("/customers/<int:customer_id>/contacts/new", methods=["POST"])
@login_required
def add_contact(customer_id):
    db = get_db()
    customer = db.execute("SELECT id FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if not customer:
        abort(404)

    name = request.form.get("name", "").strip()
    if not name:
        flash("Contact name is required.", "danger")
        return redirect(url_for("view_customer", customer_id=customer_id) + "#contacts")

    db.execute("""
        INSERT INTO contacts (customer_id, name, role, email, phone, notes, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        customer_id,
        name,
        request.form.get("role",  "").strip() or None,
        request.form.get("email", "").strip() or None,
        request.form.get("phone", "").strip() or None,
        request.form.get("notes", "").strip() or None,
        current_user.id,
    ))
    db.commit()
    flash(f"Contact '{name}' added.", "success")
    return redirect(url_for("view_customer", customer_id=customer_id) + "#contacts")


@app.route("/contacts/<int:contact_id>/edit", methods=["POST"])
@login_required
def edit_contact(contact_id):
    db = get_db()
    contact = db.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if not contact:
        abort(404)

    name = request.form.get("name", "").strip()
    if not name:
        flash("Contact name is required.", "danger")
        return redirect(url_for("view_customer", customer_id=contact["customer_id"]) + "#contacts")

    db.execute("""
        UPDATE contacts
           SET name = ?, role = ?, email = ?, phone = ?, notes = ?
         WHERE id = ?
    """, (
        name,
        request.form.get("role",  "").strip() or None,
        request.form.get("email", "").strip() or None,
        request.form.get("phone", "").strip() or None,
        request.form.get("notes", "").strip() or None,
        contact_id,
    ))
    db.commit()
    flash("Contact updated.", "success")
    return redirect(url_for("view_customer", customer_id=contact["customer_id"]) + "#contacts")


@app.route("/contacts/<int:contact_id>/delete", methods=["POST"])
@login_required
def delete_contact(contact_id):
    db = get_db()
    contact = db.execute("SELECT * FROM contacts WHERE id = ?", (contact_id,)).fetchone()
    if not contact:
        abort(404)
    db.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
    db.commit()
    flash("Contact removed.", "info")
    return redirect(url_for("view_customer", customer_id=contact["customer_id"]) + "#contacts")


# ---------------------------------------------------------------------------
# Routes: Tags
# ---------------------------------------------------------------------------
def _normalise_tag(raw: str) -> str:
    """Lowercase, collapse internal whitespace, strip outer punctuation/spaces."""
    return " ".join(raw.lower().split()).strip(" ,;")


@app.route("/customers/<int:customer_id>/tags/add", methods=["POST"])
@login_required
def add_tag(customer_id):
    db = get_db()
    if not db.execute("SELECT 1 FROM customers WHERE id = ?", (customer_id,)).fetchone():
        abort(404)

    raw = request.form.get("tags", "")
    # Allow comma-separated entry of multiple tags in one go
    parts = [_normalise_tag(p) for p in raw.split(",")]
    parts = [p for p in parts if p]  # discard empties
    if not parts:
        flash("Please type at least one tag.", "warning")
        return redirect(url_for("view_customer", customer_id=customer_id) + "#tags")

    added = 0
    for name in parts:
        # Insert into tags if new, then link to customer (idempotent)
        cur = db.execute("INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,))
        if cur.rowcount:
            tag_id = cur.lastrowid
        else:
            tag_id = db.execute("SELECT id FROM tags WHERE name = ?", (name,)).fetchone()["id"]
        link = db.execute(
            "INSERT OR IGNORE INTO customer_tags (customer_id, tag_id) VALUES (?, ?)",
            (customer_id, tag_id),
        )
        if link.rowcount:
            added += 1
    db.commit()
    if added:
        flash(f"Added {added} tag{'s' if added != 1 else ''}.", "success")
    else:
        flash("No new tags were added (already present).", "info")
    return redirect(url_for("view_customer", customer_id=customer_id) + "#tags")


@app.route("/customers/<int:customer_id>/tags/<int:tag_id>/remove", methods=["POST"])
@login_required
def remove_tag(customer_id, tag_id):
    db = get_db()
    db.execute(
        "DELETE FROM customer_tags WHERE customer_id = ? AND tag_id = ?",
        (customer_id, tag_id),
    )
    # Garbage-collect tags that nothing else uses any more
    db.execute("""
        DELETE FROM tags
        WHERE id = ?
          AND NOT EXISTS (SELECT 1 FROM customer_tags WHERE tag_id = ?)
    """, (tag_id, tag_id))
    db.commit()
    return redirect(url_for("view_customer", customer_id=customer_id) + "#tags")


# ---------------------------------------------------------------------------
# Routes: Actions
# ---------------------------------------------------------------------------
@app.route("/trackers/<int:tracker_id>/actions/new", methods=["POST"])
@login_required
def add_action(tracker_id):
    db = get_db()
    tracker = db.execute(
        "SELECT * FROM trackers WHERE id = ?", (tracker_id,)
    ).fetchone()
    if not tracker:
        abort(404)

    action_type = request.form.get("action_type", "").strip()
    notes = request.form.get("notes", "").strip()
    custom_title = request.form.get("custom_title", "").strip()

    valid_codes = [c for c, _ in get_action_choices(tracker["tracker_type"])]
    if action_type and action_type not in valid_codes:
        flash("Invalid action type for this tracker.", "danger")
        return redirect(url_for("view_customer", customer_id=tracker["customer_id"]))

    if not action_type and not notes:
        flash("Please choose an action, add a comment, or both.", "warning")
        return redirect(url_for("view_customer", customer_id=tracker["customer_id"]))

    # Allow comment-only entries by storing a special action_type "comment"
    stored_type = action_type or "comment"
    # Custom titles only apply when the user picked "Other" — for any other
    # action type the badge label is fixed by ACTION_LABELS, so a custom
    # title would just be ignored.
    stored_custom_title = custom_title if action_type == "other" and custom_title else None

    db.execute("""
        INSERT INTO actions (tracker_id, action_type, notes, custom_title, created_by)
        VALUES (?, ?, ?, ?, ?)
    """, (tracker_id, stored_type, notes or None, stored_custom_title, current_user.id))
    db.commit()
    flash("Entry added.", "success")
    return redirect(url_for("view_customer", customer_id=tracker["customer_id"]) + f"#tracker-{tracker['tracker_type']}")


@app.route("/actions/<int:action_id>/delete", methods=["POST"])
@login_required
def delete_action(action_id):
    db = get_db()
    row = db.execute("""
        SELECT a.*, t.customer_id, t.tracker_type
        FROM actions a JOIN trackers t ON t.id = a.tracker_id
        WHERE a.id = ?
    """, (action_id,)).fetchone()
    if not row:
        abort(404)
    # Author or admin only
    if row["created_by"] != current_user.id and not current_user.is_admin:
        flash("You can only delete your own entries.", "danger")
        return redirect(url_for("view_customer", customer_id=row["customer_id"]))

    db.execute("DELETE FROM actions WHERE id = ?", (action_id,))
    db.commit()
    flash("Entry deleted.", "info")
    return redirect(url_for("view_customer", customer_id=row["customer_id"]) + f"#tracker-{row['tracker_type']}")


# ---------------------------------------------------------------------------
# Routes: Dropship products checklist (AJAX-friendly)
# ---------------------------------------------------------------------------
@app.route("/trackers/<int:tracker_id>/products/<int:product_id>/toggle", methods=["POST"])
@login_required
def toggle_product(tracker_id, product_id):
    db = get_db()
    tracker = db.execute(
        "SELECT * FROM trackers WHERE id = ?", (tracker_id,)
    ).fetchone()
    if not tracker or tracker["tracker_type"] != "dropship":
        abort(404)

    row = db.execute("""
        SELECT * FROM tracker_products
        WHERE tracker_id = ? AND product_id = ?
    """, (tracker_id, product_id)).fetchone()

    now = datetime.now().isoformat(timespec="seconds")

    if row is None:
        db.execute("""
            INSERT INTO tracker_products (tracker_id, product_id, is_listed, updated_at, updated_by)
            VALUES (?, ?, 1, ?, ?)
        """, (tracker_id, product_id, now, current_user.id))
        is_listed = 1
    else:
        is_listed = 0 if row["is_listed"] else 1
        db.execute("""
            UPDATE tracker_products
               SET is_listed = ?, updated_at = ?, updated_by = ?
             WHERE id = ?
        """, (is_listed, now, current_user.id, row["id"]))
    db.commit()
    return jsonify({
        "ok": True,
        "is_listed": bool(is_listed),
        "updated_at_display": _format_dt(now),
        "updated_by": current_user.username,
    })


# ---------------------------------------------------------------------------
# Routes: Admin (users & products)
# ---------------------------------------------------------------------------
@app.route("/admin")
@login_required
@admin_required
def admin_home():
    db = get_db()
    users = db.execute(
        "SELECT id, username, is_admin, created_at FROM users ORDER BY username"
    ).fetchall()
    products = db.execute(
        "SELECT * FROM products ORDER BY is_active DESC, name COLLATE NOCASE"
    ).fetchall()
    return render_template("admin.html", users=users, products=products)


@app.route("/admin/users/new", methods=["POST"])
@login_required
@admin_required
def admin_new_user():
    username = request.form.get("username", "").strip()
    password = request.form.get("password", "")
    is_admin = 1 if request.form.get("is_admin") else 0
    if not username or not password:
        flash("Username and password are required.", "danger")
        return redirect(url_for("admin_home"))

    db = get_db()
    try:
        db.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, ?)",
            (username, generate_password_hash(password), is_admin),
        )
        db.commit()
        flash(f"User '{username}' created.", "success")
    except sqlite3.IntegrityError:
        flash("That username is already taken.", "danger")
    return redirect(url_for("admin_home"))


@app.route("/admin/users/<int:user_id>/reset_password", methods=["POST"])
@login_required
@admin_required
def admin_reset_password(user_id):
    new_password = request.form.get("new_password", "")
    if not new_password:
        flash("Password cannot be empty.", "danger")
        return redirect(url_for("admin_home"))
    db = get_db()
    db.execute(
        "UPDATE users SET password_hash = ? WHERE id = ?",
        (generate_password_hash(new_password), user_id),
    )
    db.commit()
    flash("Password reset.", "success")
    return redirect(url_for("admin_home"))


@app.route("/admin/users/<int:user_id>/delete", methods=["POST"])
@login_required
@admin_required
def admin_delete_user(user_id):
    if user_id == current_user.id:
        flash("You can't delete your own account.", "danger")
        return redirect(url_for("admin_home"))
    db = get_db()
    db.execute("DELETE FROM users WHERE id = ?", (user_id,))
    db.commit()
    flash("User deleted.", "info")
    return redirect(url_for("admin_home"))


@app.route("/admin/products/new", methods=["POST"])
@login_required
@admin_required
def admin_new_product():
    name = request.form.get("name", "").strip()
    if not name:
        flash("Product name required.", "danger")
        return redirect(url_for("admin_home"))
    db = get_db()
    try:
        db.execute("INSERT INTO products (name, is_active) VALUES (?, 1)", (name,))
        db.commit()
        flash(f"Added product '{name}'.", "success")
    except sqlite3.IntegrityError:
        flash("That product already exists.", "danger")
    return redirect(url_for("admin_home"))


@app.route("/admin/products/<int:product_id>/toggle", methods=["POST"])
@login_required
@admin_required
def admin_toggle_product(product_id):
    db = get_db()
    row = db.execute("SELECT is_active FROM products WHERE id = ?", (product_id,)).fetchone()
    if not row:
        abort(404)
    new_state = 0 if row["is_active"] else 1
    db.execute("UPDATE products SET is_active = ? WHERE id = ?", (new_state, product_id))
    db.commit()
    return redirect(url_for("admin_home"))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    if not os.path.exists(DB_PATH):
        print(f"ERROR: database not found at {DB_PATH}. Run `python init_db.py` first.")
        raise SystemExit(1)
    # host=0.0.0.0 makes the server reachable from other machines on the LAN.
    # PORT comes from the environment when deployed (e.g. Render sets this
    # automatically); falls back to 5000 for local Windows runs.
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
