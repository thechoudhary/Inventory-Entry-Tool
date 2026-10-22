import hashlib
import hmac
import io
import os
import re
import secrets
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

APP_DIR = Path(__file__).resolve().parent
DB_PATH = APP_DIR / "inventory.db"
MASTER_TABLES = {
    "Category": {"table": "categories", "code_col": "category_code", "name_col": "category_name"},
    "Brand": {"table": "brands", "code_col": "brand_code", "name_col": "brand_name"},
    "Colour": {"table": "colours", "code_col": "colour_code", "name_col": "colour_name"},
    "Grade": {"table": "grades", "code_col": "grade_code", "name_col": "grade_name"},
}
PBKDF2_ITERATIONS = 310_000


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def hash_password(password, salt=None):
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ITERATIONS).hex()
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest}"


def verify_password(password, stored):
    try:
        algorithm, iterations, salt, expected = stored.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), int(iterations)).hex()
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def add_column_if_missing(conn, table, column, definition):
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db():
    with connect() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL COLLATE NOCASE UNIQUE,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL CHECK(role IN ('admin', 'user')),
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS categories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            category_code TEXT NOT NULL COLLATE NOCASE UNIQUE,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL DEFAULT 'system'
        );
        CREATE TABLE IF NOT EXISTS brands (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            brand_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            brand_code TEXT NOT NULL COLLATE NOCASE UNIQUE,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL DEFAULT 'system'
        );
        CREATE TABLE IF NOT EXISTS colours (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            colour_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            colour_code TEXT NOT NULL COLLATE NOCASE UNIQUE,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL DEFAULT 'system'
        );
        CREATE TABLE IF NOT EXISTS grades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            grade_name TEXT NOT NULL COLLATE NOCASE UNIQUE,
            grade_code TEXT NOT NULL COLLATE NOCASE UNIQUE,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL DEFAULT 'system'
        );
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_name TEXT NOT NULL,
            model_name TEXT NOT NULL,
            category_id INTEGER NOT NULL,
            brand_id INTEGER NOT NULL,
            grade_id INTEGER NOT NULL,
            colour_id INTEGER NOT NULL,
            mrp REAL NOT NULL CHECK(mrp >= 0),
            purchase_price REAL NOT NULL CHECK(purchase_price >= 0),
            selling_price REAL NOT NULL CHECK(selling_price >= 0),
            source_url TEXT,
            sku TEXT NOT NULL COLLATE NOCASE UNIQUE,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL DEFAULT 'system',
            FOREIGN KEY(category_id) REFERENCES categories(id),
            FOREIGN KEY(brand_id) REFERENCES brands(id),
            FOREIGN KEY(grade_id) REFERENCES grades(id),
            FOREIGN KEY(colour_id) REFERENCES colours(id)
        );
        """)
        # Migrate databases created by earlier versions of this app.
        for table in ("categories", "brands", "colours", "grades"):
            add_column_if_missing(conn, table, "created_by", "TEXT NOT NULL DEFAULT 'system'")
        add_column_if_missing(conn, "inventory", "source_url", "TEXT")
        add_column_if_missing(conn, "inventory", "created_by", "TEXT NOT NULL DEFAULT 'system'")
        add_column_if_missing(conn, "inventory", "created_at", "TEXT NOT NULL DEFAULT ''")
        add_column_if_missing(conn, "users", "created_at", "TEXT NOT NULL DEFAULT ''")
        add_column_if_missing(conn, "users", "created_by", "TEXT NOT NULL DEFAULT 'system'")
        # A product can have multiple colours. Keep colour_id for compatibility with
        # older databases and store all selected colours in this relation table.
        conn.execute("""CREATE TABLE IF NOT EXISTS inventory_colours (
            inventory_id INTEGER NOT NULL,
            colour_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            created_by TEXT NOT NULL,
            PRIMARY KEY (inventory_id, colour_id),
            FOREIGN KEY(inventory_id) REFERENCES inventory(id) ON DELETE CASCADE,
            FOREIGN KEY(colour_id) REFERENCES colours(id)
        )""")
        # Backfill the original single colour for products created by earlier versions.
        conn.execute("""INSERT OR IGNORE INTO inventory_colours (inventory_id, colour_id, created_at, created_by)
            SELECT id, colour_id, created_at, created_by FROM inventory""")


def clean_code(code):
    return re.sub(r"[^A-Za-z0-9_-]", "", str(code).strip()).upper()


def model_abbreviation(model_name):
    words = re.findall(r"[A-Za-z0-9]+", model_name.upper())
    if not words:
        return ""
    if len(words) == 1:
        return words[0][:8]
    return "".join(w[:2] for w in words)[:10]


def make_sku(category_code, brand_code, model_name, colour_codes, grade_code):
    # Multiple colours are represented in the colour SKU segment, joined with '+'.
    if isinstance(colour_codes, str):
        colour_codes = [colour_codes]
    # colour_segment = "+".join(clean_code(code) for code in colour_codes)
    sorted_colours = sorted(selected_colours, key=lambda c: c["name"].casefold())

    combined_colour_code = "".join(
        colour["code"].replace(" ", "").upper()
        for colour in sorted_colours
    )
    return "-".join([clean_code(category_code), clean_code(brand_code), model_abbreviation(model_name), combined_colour_code, clean_code(grade_code)])


def get_master_options(master_key):
    meta = MASTER_TABLES[master_key]
    with connect() as conn:
        rows = conn.execute(f"SELECT id, {meta['name_col']} AS name, {meta['code_col']} AS code FROM {meta['table']} ORDER BY {meta['name_col']}").fetchall()
    return [dict(r) for r in rows]


def add_master(master_key, name, code, username):
    meta = MASTER_TABLES[master_key]
    name = str(name).strip()
    code = clean_code(code)
    if not name or not code:
        raise ValueError("Both name and code are required.")
    if not re.fullmatch(r"[A-Z0-9_-]+", code):
        raise ValueError("Code may contain only letters, numbers, hyphens, and underscores.")
    with connect() as conn:
        conn.execute(f"INSERT INTO {meta['table']} ({meta['name_col']}, {meta['code_col']}, created_at, created_by) VALUES (?, ?, ?, ?)", (name, code, now_iso(), username))


def add_user(username, password, role, created_by):
    username = username.strip()
    if len(username) < 3:
        raise ValueError("Username must contain at least 3 characters.")
    if len(password) < 5:
        raise ValueError("Password must contain at least 5 characters.")
    if role not in ("admin", "user"):
        raise ValueError("Invalid role.")
    with connect() as conn:
        conn.execute("INSERT INTO users (username, password_hash, role, created_at, created_by) VALUES (?, ?, ?, ?, ?)", (username, hash_password(password), role, now_iso(), created_by))


def fetch_inventory(search=""):
    query = '''SELECT i.id, i.item_name AS "Item Name", i.model_name AS "Model Name",
        c.category_name AS "Item Category", b.brand_name AS Brand, g.grade_name AS Grade,
        COALESCE((SELECT GROUP_CONCAT(co.colour_name, ', ')
            FROM inventory_colours ic JOIN colours co ON co.id=ic.colour_id
            WHERE ic.inventory_id=i.id), (SELECT colour_name FROM colours WHERE id=i.colour_id)) AS Colour,
        i.mrp AS MRP, i.purchase_price AS "Purchase Price",
        i.selling_price AS "Selling Price", i.source_url AS "Source URL", i.sku AS SKU,
        i.created_at AS created_at, i.created_by AS created_by
        FROM inventory i JOIN categories c ON c.id=i.category_id
        JOIN brands b ON b.id=i.brand_id JOIN grades g ON g.id=i.grade_id'''
    params = ()
    if search.strip():
        query += " WHERE i.item_name LIKE ? OR i.model_name LIKE ? OR i.sku LIKE ? OR c.category_name LIKE ? OR b.brand_name LIKE ?"
        term = f"%{search.strip()}%"
        params = (term, term, term, term, term)
    query += " ORDER BY i.id DESC"
    with connect() as conn:
        return pd.read_sql_query(query, conn, params=params)


def add_inventory(values, colour_ids):
    with connect() as conn:
        cur = conn.execute("""INSERT INTO inventory (item_name, model_name, category_id, brand_id, grade_id, colour_id,
        mrp, purchase_price, selling_price, source_url, sku, created_at, created_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values)
        inventory_id = cur.lastrowid
        for colour_id in colour_ids:
            conn.execute("INSERT INTO inventory_colours (inventory_id, colour_id, created_at, created_by) VALUES (?, ?, ?, ?)",
                         (inventory_id, colour_id, now_iso(), values[-1]))


def add_bulk_master_csv(master_key, uploaded_file, username):
    df = pd.read_csv(uploaded_file, dtype=str).fillna("")
    normalized = {str(c).strip().lower(): c for c in df.columns}
    name_col = next((normalized[k] for k in ("name", "category_name", "brand_name", "colour_name", "color_name", "grade_name") if k in normalized), None)
    code_col = next((normalized[k] for k in ("code", "category_code", "brand_code", "colour_code", "color_code", "grade_code") if k in normalized), None)
    if not name_col or not code_col:
        raise ValueError("CSV must contain Name and Code columns (or the matching master-specific column names).")
    added, errors = 0, []
    for idx, row in df.iterrows():
        try:
            add_master(master_key, row[name_col], row[code_col], username)
            added += 1
        except (ValueError, sqlite3.IntegrityError) as exc:
            errors.append(f"Row {idx + 2}: {exc}")
    return added, errors


st.set_page_config(page_title="Inventory Entry Tool", page_icon="📦", layout="wide")
init_db()
st.title("📦 Inventory Entry Tool")
st.caption("Secure inventory entry, role-based access, dropdown management, and SQLite storage.")

# First-run setup: no shared/default password is created. The first account becomes admin.
with connect() as conn:
    user_count = conn.execute("SELECT COUNT(*) AS n FROM users").fetchone()["n"]

if user_count == 0:
    st.info("First-time setup: create the initial administrator account. This screen disappears after the first admin is created.")
    with st.form("initial_admin_form"):
        setup_username = st.text_input("Admin username")
        setup_password = st.text_input("Admin password", type="password", help="Use at least 10 characters.")
        setup_confirm = st.text_input("Confirm password", type="password")
        setup_submit = st.form_submit_button("Create Admin", type="primary")
    if setup_submit:
        if setup_password != setup_confirm:
            st.error("Passwords do not match.")
        else:
            try:
                add_user(setup_username, setup_password, "admin", "system")
                st.success("Admin created. Please sign in.")
                st.rerun()
            except (ValueError, sqlite3.IntegrityError) as exc:
                st.error(str(exc) if isinstance(exc, ValueError) else "That username already exists.")
    st.stop()

if "auth_user" not in st.session_state:
    st.session_state.auth_user = None

if not st.session_state.auth_user:
    with st.sidebar:
        st.subheader("Sign in")
        with st.form("login_form"):
            login_username = st.text_input("Username")
            login_password = st.text_input("Password", type="password")
            login_submit = st.form_submit_button("Login", type="primary", use_container_width=True)
        if login_submit:
            with connect() as conn:
                user = conn.execute("SELECT username, password_hash, role FROM users WHERE username = ? COLLATE NOCASE", (login_username.strip(),)).fetchone()
            if user and verify_password(login_password, user["password_hash"]):
                st.session_state.auth_user = {"username": user["username"], "role": user["role"]}
                st.rerun()
            else:
                st.error("Invalid username or password.")
    st.title("Please sign in to continue")
    st.stop()

current_user = st.session_state.auth_user
is_admin = current_user["role"] == "admin"
with st.sidebar:
    st.success(f"Signed in: {current_user['username']} ({current_user['role']})")
    if st.button("Log out", use_container_width=True):
        st.session_state.auth_user = None
        st.rerun()

pages = ["➕ Add Product", "📋 Inventory"]
if is_admin:
    pages += ["⚙️ Manage Dropdowns", "👥 Manage Users"]
page = st.radio("Navigation", pages, horizontal=True, label_visibility="collapsed")

if page == "➕ Add Product":
    st.subheader("Product details")
    categories, brands = get_master_options("Category"), get_master_options("Brand")
    colours, grades = get_master_options("Colour"), get_master_options("Grade")
    if not all([categories, brands, colours, grades]):
        st.warning("An administrator must add at least one Category, Brand, Colour, and Grade before products can be entered.")
    else:
        with st.form("product_form", clear_on_submit=True):
            col1, col2, col3 = st.columns(3)
            with col1:
                item_name = st.text_input("Item Name *")
                model_name = st.text_input("Model Name *")
                category = st.selectbox("Item Category *", [None] + categories, format_func=lambda x: "Select category" if x is None else f"{x['name']} ({x['code']})")
            with col2:
                brand = st.selectbox("Brand *", [None] + brands, format_func=lambda x: "Select brand" if x is None else f"{x['name']} ({x['code']})")
                selected_colours = st.multiselect("Colour(s) *", colours, format_func=lambda x: f"{x['name']} ({x['code']})", help="Select one or more colours for this product.")
                grade = st.selectbox("Grade *", [None] + grades, format_func=lambda x: "Select grade" if x is None else f"{x['name']} ({x['code']})")
            with col3:
                mrp = st.number_input("MRP *", min_value=0.0, step=1.0, format="%.2f")
                purchase_price = st.number_input("Purchase Price *", min_value=0.0, step=1.0, format="%.2f")
                selling_price = st.number_input("Selling Price *", min_value=0.0, step=1.0, format="%.2f")
            source_url = st.text_input("Source URL (optional)", placeholder="https://...")
            colour_codes = [c["code"] for c in selected_colours]
            sku = make_sku(category["code"], brand["code"], model_name, colour_codes, grade["code"]) if all([category, brand, grade]) and selected_colours and model_name.strip() else ""
            st.text_input("Generated SKU", value=sku, disabled=True)
            submitted = st.form_submit_button("Save Product", type="primary", use_container_width=True)
        if submitted:
            if not item_name.strip() or not model_name.strip() or not all([category, brand, grade]) or not selected_colours:
                st.error("Complete all required fields and select at least one colour.")
            elif source_url.strip() and not re.match(r"^https?://", source_url.strip(), re.I):
                st.error("Source URL must start with http:// or https://, or be left empty.")
            else:
                try:
                    add_inventory((item_name.strip(), model_name.strip(), category["id"], brand["id"], grade["id"], selected_colours[0]["id"], float(mrp), float(purchase_price), float(selling_price), source_url.strip() or None, sku, now_iso(), current_user["username"]), [c["id"] for c in selected_colours])
                    st.success(f"Product saved. SKU: {sku}")
                except sqlite3.IntegrityError as exc:
                    if "unique" in str(exc).lower():
                        st.error(f"SKU already exists: {sku}. Adjust the model name or master codes.")
                    else:
                        st.error(f"Could not save product: {exc}")

elif page == "⚙️ Manage Dropdowns" and is_admin:
    st.subheader("Manage dropdown values")
    st.write("Add values one at a time or upload a CSV. Names and codes must both be unique. The app records creation time and user.")
    master_choice = st.selectbox("Select section", list(MASTER_TABLES.keys()))
    with st.form("master_form", clear_on_submit=True):
        new_name = st.text_input(f"{master_choice} Name *")
        new_code = st.text_input(f"{master_choice} Code *", help="Letters, numbers, hyphens, and underscores only.")
        master_submit = st.form_submit_button(f"Add {master_choice}", type="primary")
    if master_submit:
        try:
            add_master(master_choice, new_name, new_code, current_user["username"])
            st.success(f"{master_choice} added successfully.")
        except sqlite3.IntegrityError:
            st.error("That name or code already exists. Use a unique value.")
        except ValueError as exc:
            st.error(str(exc))
    st.divider()
    st.subheader("Bulk upload via CSV")
    st.caption("Required columns: Name, Code. You can also use category_name/category_code, brand_name/brand_code, colour_name/colour_code, or grade_name/grade_code.")
    template = pd.DataFrame([{"Name": "Example Name", "Code": "EXM"}]).to_csv(index=False).encode("utf-8")
    st.download_button("Download CSV template", template, file_name=f"{master_choice.lower()}_template.csv", mime="text/csv")
    uploaded_csv = st.file_uploader("Upload CSV", type=["csv"], key=f"bulk_{master_choice}")
    if uploaded_csv and st.button(f"Import {master_choice} CSV", type="primary"):
        try:
            added, errors = add_bulk_master_csv(master_choice, uploaded_csv, current_user["username"])
            st.success(f"Imported {added} {master_choice.lower()} value(s).")
            if errors:
                st.warning(f"{len(errors)} row(s) were skipped:")
                st.code("\n".join(errors[:50]))
        except Exception as exc:
            st.error(f"Could not process CSV: {exc}")
    st.divider()
    meta = MASTER_TABLES[master_choice]
    with connect() as conn:
        data = pd.read_sql_query(f"SELECT {meta['name_col']} AS Name, {meta['code_col']} AS Code, created_at, created_by FROM {meta['table']} ORDER BY id DESC", conn)
    st.subheader(f"Existing {master_choice} values")
    st.dataframe(data, use_container_width=True, hide_index=True)

elif page == "👥 Manage Users" and is_admin:
    st.subheader("Manage users")
    with st.form("add_user_form", clear_on_submit=True):
        new_username = st.text_input("Username *")
        new_password = st.text_input("Temporary password *", type="password", help="At least 10 characters.")
        new_password_confirm = st.text_input("Confirm password *", type="password")
        new_role = st.selectbox("Role *", ["user", "admin"], format_func=lambda r: "User (product entry and inventory view)" if r == "user" else "Admin (all permissions)")
        create_user_submit = st.form_submit_button("Create User", type="primary")
    if create_user_submit:
        if new_password != new_password_confirm:
            st.error("Passwords do not match.")
        else:
            try:
                add_user(new_username, new_password, new_role, current_user["username"])
                st.success(f"User '{new_username.strip()}' created with role '{new_role}'.")
            except sqlite3.IntegrityError:
                st.error("That username already exists.")
            except ValueError as exc:
                st.error(str(exc))
    with connect() as conn:
        users_df = pd.read_sql_query("SELECT username, role, created_at, created_by FROM users ORDER BY id DESC", conn)
    st.subheader("Existing users")
    st.dataframe(users_df, use_container_width=True, hide_index=True)

elif page == "📋 Inventory":
    st.subheader("Saved inventory")
    search = st.text_input("Search by item, model, SKU, category, or brand")
    inventory_df = fetch_inventory(search)
    if inventory_df.empty:
        st.info("No products found. Add your first product in the Add Product section.")
    else:
        st.metric("Products shown", len(inventory_df))
        st.download_button("⬇️ Download CSV", inventory_df.to_csv(index=False).encode("utf-8-sig"), file_name="inventory.csv", mime="text/csv")
        st.dataframe(inventory_df, use_container_width=True, hide_index=True)

st.sidebar.header("SKU format")
st.sidebar.code("CATEGORY-BRAND-MODEL-COLOUR1+COLOUR2-GRADE")
st.sidebar.caption("Select multiple colours if needed; their codes are joined with '+'. Model abbreviation: one word = first 8 characters; multiple words = first 2 characters of each word (up to 10).")
st.sidebar.info(f"SQLite database: {DB_PATH.name}")
