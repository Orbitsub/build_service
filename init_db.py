"""
init_db.py — Create all application tables and seed site_config defaults.

Usage (run once on a fresh install, or again to add missing tables/rows):
    python init_db.py [options]

Options:
    --db PATH               Path to the SQLite database file (default: ./mydatabase.db)
    --webhook URL           Discord webhook URL
    --redirect-uri URI      EVE SSO OAuth redirect URI
    --alliance-id ID        EVE Alliance ID to enforce access for (default: 498125261)
    --delivery-default STR  Default pickup/delivery location text
    --markup-pct NUM        Default build markup percentage (default: 15)

NOTE: inv_types and inv_groups are created as empty tables here.
      Populate them with the EVE Static Data Export (SDE) for item search to work.
      Download: https://developers.eveonline.com/resource/resources
"""
import argparse
import os
import sqlite3

SCHEMA = """
-- ── Site configuration ──────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS site_config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);

-- ── Doctrine fits ────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS doctrine_fits (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    fit_name   TEXT NOT NULL,
    ship_name  TEXT NOT NULL,
    ship_class TEXT NOT NULL DEFAULT ''
);

-- ── Doctrine fit line items ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS doctrine_fit_items (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    fit_id    INTEGER NOT NULL REFERENCES doctrine_fits(id) ON DELETE CASCADE,
    type_id   INTEGER,
    item_name TEXT NOT NULL,
    quantity  INTEGER NOT NULL DEFAULT 1
);

-- ── Build requests ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS build_requests (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at        TEXT    NOT NULL DEFAULT (datetime('now')),
    status            TEXT    NOT NULL DEFAULT 'pending',
    lookup_token      TEXT    NOT NULL UNIQUE,
    customer_name     TEXT    NOT NULL,
    character_id      INTEGER,
    character_name    TEXT,
    item_type_id      INTEGER,
    item_name         TEXT    NOT NULL,
    quantity          INTEGER NOT NULL DEFAULT 1,
    is_doctrine_fit   INTEGER NOT NULL DEFAULT 0,
    doctrine_fit_id   INTEGER,
    delivery_location TEXT,
    deadline          TEXT,
    notes             TEXT,
    markup_pct        REAL    NOT NULL DEFAULT 15.0,
    quoted_price      REAL,
    quoted_at         TEXT,
    accepted_at       TEXT,
    completed_at      TEXT
);

-- ── Build request line items (doctrine fits expand to these) ─────────────────
CREATE TABLE IF NOT EXISTS build_request_items (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id INTEGER NOT NULL REFERENCES build_requests(id) ON DELETE CASCADE,
    type_id    INTEGER,
    item_name  TEXT    NOT NULL,
    quantity   INTEGER NOT NULL DEFAULT 1
);

-- ── EVE item types (populated from EVE SDE — see module docstring) ───────────
CREATE TABLE IF NOT EXISTS inv_types (
    type_id   INTEGER PRIMARY KEY,
    type_name TEXT    NOT NULL,
    group_id  INTEGER,
    published INTEGER NOT NULL DEFAULT 0
);

-- ── EVE item groups (populated from EVE SDE — see module docstring) ──────────
CREATE TABLE IF NOT EXISTS inv_groups (
    group_id   INTEGER PRIMARY KEY,
    group_name TEXT    NOT NULL
);
"""

# site_config keys inserted on first run; existing rows are never overwritten.
DEFAULT_CONFIG = {
    "build_alliance_id":        "498125261",
    "build_discord_webhook":    "",
    "build_redirect_uri":       "http://localhost:5000/auth/callback",
    "build_delivery_default":   "",
    "build_default_markup_pct": "15",
}


def init_db(db_path: str, overrides: dict) -> None:
    db = sqlite3.connect(db_path)
    db.executescript(SCHEMA)
    db.execute("PRAGMA journal_mode=WAL")  # better concurrency under Gunicorn

    config = {**DEFAULT_CONFIG, **{k: v for k, v in overrides.items() if v}}
    for key, value in config.items():
        db.execute(
            "INSERT OR IGNORE INTO site_config (key, value) VALUES (?, ?)",
            (key, value),
        )

    db.commit()
    db.close()
    print(f"[OK] Database initialised: {db_path}")


def main() -> None:
    default_db = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mydatabase.db")

    parser = argparse.ArgumentParser(description="Initialise the Build Service database.")
    parser.add_argument("--db",               default=default_db,  help="Path to SQLite DB file")
    parser.add_argument("--webhook",          default="",          help="Discord webhook URL")
    parser.add_argument("--redirect-uri",     default="",          help="EVE SSO redirect URI")
    parser.add_argument("--alliance-id",      default="",          help="EVE Alliance ID")
    parser.add_argument("--delivery-default", default="",          help="Default delivery location")
    parser.add_argument("--markup-pct",       default="",          help="Default build markup %")
    args = parser.parse_args()

    overrides = {
        "build_discord_webhook":    args.webhook,
        "build_redirect_uri":       args.redirect_uri,
        "build_alliance_id":        args.alliance_id,
        "build_delivery_default":   args.delivery_default,
        "build_default_markup_pct": args.markup_pct,
    }

    init_db(args.db, overrides)
    print("[INFO] inv_types and inv_groups are empty. Import the EVE SDE to enable item search.")
    print("       Download from: https://developers.eveonline.com/resource/resources")


if __name__ == "__main__":
    main()
