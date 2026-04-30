"""
Database initialization script.
Run this once before starting the app: python init_db.py

For non-interactive (Render etc.) deploys, set these env vars:
  DATABASE_PATH         — where to write the .db file (default ./database.db)
  INIT_ADMIN_USERNAME   — admin username to create on first run
  INIT_ADMIN_PASSWORD   — admin password to create on first run
"""
import sqlite3
import os
import sys
from werkzeug.security import generate_password_hash
from getpass import getpass

DB_PATH = os.environ.get("DATABASE_PATH") or "database.db"

# Default product list for the Dropship "Products Listed" checklist.
# You can edit this list, or add/remove products later from the admin page.
DEFAULT_PRODUCTS = [
    "Car covers",
    "Boat covers",
    "Boot liners",
    "Bike covers",
    "Motorcycle covers",
    "Caravan covers",
    "BBQ covers",
    "Garden furniture covers",
    "Trailer covers",
]

# The four tracker types every customer gets
TRACKER_TYPES = [
    ("dropship", "Dropship"),
    ("supply_only", "Supply Only"),
    ("build_your_brand", "Build Your Brand"),
    ("white_label", "White Label"),
]


def init_db():
    """Create all tables and seed default data."""
    new_db = not os.path.exists(DB_PATH)
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    c = conn.cursor()

    # Users table -------------------------------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            username      TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            is_admin      INTEGER NOT NULL DEFAULT 0,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Customers table ---------------------------------------------------
    # Note: contact_name/email/phone columns are kept for backward
    # compatibility with older versions of this app — but new contact
    # data is stored in the `contacts` table below.
    c.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            contact_name  TEXT,
            contact_email TEXT,
            contact_phone TEXT,
            notes         TEXT,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by    INTEGER,
            FOREIGN KEY (created_by) REFERENCES users(id)
        )
    """)

    # Contacts table (multiple contacts per customer) -------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS contacts (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id INTEGER NOT NULL,
            name        TEXT NOT NULL,
            role        TEXT,
            email       TEXT,
            phone       TEXT,
            notes       TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by  INTEGER,
            FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
            FOREIGN KEY (created_by) REFERENCES users(id)
        )
    """)

    # Migrate any old single-contact data from the customers table
    # into the new contacts table.  Runs once: it only inserts if a
    # customer has no contacts yet. Guarded with a column check because
    # newer databases never had the legacy contact_* columns to begin
    # with — running the SELECT against a missing column would crash.
    cust_cols = {row[1] for row in c.execute("PRAGMA table_info(customers)").fetchall()}
    if {"contact_name", "contact_email", "contact_phone"} <= cust_cols:
        c.execute("""
            INSERT INTO contacts (customer_id, name, email, phone, created_at)
            SELECT
                cu.id,
                COALESCE(NULLIF(TRIM(cu.contact_name), ''), '(unnamed)'),
                cu.contact_email,
                cu.contact_phone,
                cu.created_at
            FROM customers cu
            WHERE (
                  (cu.contact_name  IS NOT NULL AND TRIM(cu.contact_name)  != '')
               OR (cu.contact_email IS NOT NULL AND TRIM(cu.contact_email) != '')
               OR (cu.contact_phone IS NOT NULL AND TRIM(cu.contact_phone) != '')
            )
            AND NOT EXISTS (SELECT 1 FROM contacts WHERE customer_id = cu.id)
        """)

    # Trackers table (4 per customer, auto-created) ---------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS trackers (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            customer_id  INTEGER NOT NULL,
            tracker_type TEXT NOT NULL,
            UNIQUE (customer_id, tracker_type),
            FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE
        )
    """)

    # Actions / log entries on each tracker -----------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS actions (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tracker_id  INTEGER NOT NULL,
            action_type TEXT NOT NULL,
            notes       TEXT,
            created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            created_by  INTEGER,
            FOREIGN KEY (tracker_id) REFERENCES trackers(id) ON DELETE CASCADE,
            FOREIGN KEY (created_by) REFERENCES users(id)
        )
    """)

    # Master product list (used by Dropship "Products Listed" checklist)
    c.execute("""
        CREATE TABLE IF NOT EXISTS products (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            name      TEXT UNIQUE NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1
        )
    """)

    # Per-tracker product checklist state -------------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS tracker_products (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            tracker_id  INTEGER NOT NULL,
            product_id  INTEGER NOT NULL,
            is_listed   INTEGER NOT NULL DEFAULT 0,
            updated_at  TIMESTAMP,
            updated_by  INTEGER,
            UNIQUE (tracker_id, product_id),
            FOREIGN KEY (tracker_id) REFERENCES trackers(id) ON DELETE CASCADE,
            FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
            FOREIGN KEY (updated_by) REFERENCES users(id)
        )
    """)

    # Tags (custom labels, case-insensitive) ----------------------------
    c.execute("""
        CREATE TABLE IF NOT EXISTS tags (
            id   INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL  -- always stored lowercase
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS customer_tags (
            customer_id INTEGER NOT NULL,
            tag_id      INTEGER NOT NULL,
            PRIMARY KEY (customer_id, tag_id),
            FOREIGN KEY (customer_id) REFERENCES customers(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id)      REFERENCES tags(id)      ON DELETE CASCADE
        )
    """)

    # Migration: add custom_title to actions for "Other"-typed entries.
    # Using a defensive ADD-COLUMN-IF-NOT-EXISTS pattern via PRAGMA so it's
    # safe to re-run init_db.py on existing databases.
    cols = {row[1] for row in c.execute("PRAGMA table_info(actions)").fetchall()}
    if "custom_title" not in cols:
        c.execute("ALTER TABLE actions ADD COLUMN custom_title TEXT")

    # Seed default products if table is empty
    c.execute("SELECT COUNT(*) FROM products")
    if c.fetchone()[0] == 0:
        c.executemany(
            "INSERT INTO products (name, is_active) VALUES (?, 1)",
            [(p,) for p in DEFAULT_PRODUCTS],
        )
        print(f"Seeded {len(DEFAULT_PRODUCTS)} default products.")

    conn.commit()

    # If this is a brand new database, create an admin user. Two paths:
    # 1) Env-driven (cloud/CI):
    #      INIT_ADMIN_USERNAME=admin INIT_ADMIN_PASSWORD=... python init_db.py
    #    On Render, set these as service environment variables.
    # 2) Interactive (local/Windows): prompt for a username + password.
    if new_db:
        env_user = os.environ.get("INIT_ADMIN_USERNAME", "").strip()
        env_pw   = os.environ.get("INIT_ADMIN_PASSWORD", "")

        if env_user and env_pw:
            print(f"\nCreating admin user '{env_user}' from environment variables.")
            username = env_user
            password = env_pw
        elif not sys.stdin.isatty():
            # Running headless without env vars set — fail loudly rather
            # than hang forever waiting for input that will never come.
            print("\nERROR: brand-new database but no admin credentials provided.")
            print("Set INIT_ADMIN_USERNAME and INIT_ADMIN_PASSWORD in the environment.")
            conn.close()
            raise SystemExit(1)
        else:
            print("\nNo users exist. Let's create an admin account.")
            while True:
                username = input("Admin username: ").strip()
                if username:
                    break
            while True:
                password = getpass("Admin password: ")
                confirm  = getpass("Confirm password: ")
                if password and password == confirm:
                    break
                print("Passwords don't match or are empty. Try again.")

        c.execute(
            "INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)",
            (username, generate_password_hash(password)),
        )
        conn.commit()
        print(f"Admin user '{username}' created.")

    conn.close()
    print("\nDatabase ready: " + os.path.abspath(DB_PATH))


if __name__ == "__main__":
    init_db()
