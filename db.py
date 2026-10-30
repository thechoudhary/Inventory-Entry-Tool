import sqlite3, os, json, re
from pathlib import Path
from datetime import datetime, timezone
from hashlib import scrypt
import hmac
from sku import generate_sku, colour_code, generate_product_handle, extract_physical_from_specs

def _default_db_path():
    # Streamlit Cloud mounts the app in a read-only directory; /tmp is always writable.
    # Locally the DB sits next to this file (original behaviour).
    if os.environ.get("STREAMLIT_SHARING_MODE") or os.environ.get("IS_STREAMLIT_CLOUD"):
        return Path("/tmp/inventory.db")
    return Path(__file__).with_name("inventory.db")

DB_PATH = Path(os.environ.get("INVENTORY_DB_PATH", _default_db_path()))

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
          password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','standard')),
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          created_by TEXT, modified_by TEXT);
        CREATE TABLE IF NOT EXISTS categories(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
          code TEXT NOT NULL UNIQUE COLLATE NOCASE,
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          created_by TEXT, modified_by TEXT);
        CREATE TABLE IF NOT EXISTS sub_categories(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
          code TEXT NOT NULL UNIQUE COLLATE NOCASE,
          parent_id INTEGER NOT NULL REFERENCES categories(id),
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          created_by TEXT, modified_by TEXT);
        CREATE TABLE IF NOT EXISTS brands(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
          code TEXT NOT NULL UNIQUE COLLATE NOCASE,
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          created_by TEXT, modified_by TEXT);
        CREATE TABLE IF NOT EXISTS grades(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
          code TEXT NOT NULL UNIQUE COLLATE NOCASE,
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          created_by TEXT, modified_by TEXT);
        CREATE TABLE IF NOT EXISTS colours(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE,
          code TEXT NOT NULL UNIQUE COLLATE NOCASE,
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          created_by TEXT, modified_by TEXT);
        CREATE TABLE IF NOT EXISTS abbreviations(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, code TEXT NOT NULL,
          priority INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          created_by TEXT, modified_by TEXT);
        CREATE TABLE IF NOT EXISTS inventory(
          id INTEGER PRIMARY KEY, item_name TEXT NOT NULL, model_name TEXT NOT NULL DEFAULT '',
          category_id INTEGER NOT NULL REFERENCES categories(id),
          sub_category_id INTEGER REFERENCES sub_categories(id),
          brand_id INTEGER NOT NULL REFERENCES brands(id),
          grade_id INTEGER REFERENCES grades(id), colour_code TEXT NOT NULL DEFAULT '',
          colours_json TEXT NOT NULL DEFAULT '[]', quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity>=0),
          mrp REAL, purchase_price REAL, selling_price REAL, sku TEXT NOT NULL UNIQUE COLLATE NOCASE,
          lot_number TEXT, source_url TEXT, osp REAL, capacity INTEGER, capacity_unit TEXT,
          amazon_asin TEXT, amazon_title TEXT, amazon_price TEXT, amazon_rating TEXT,
          amazon_reviews TEXT, amazon_availability TEXT, description TEXT,
          amazon_about_this_item TEXT, specifications_json TEXT NOT NULL DEFAULT '{}',
          images_json TEXT NOT NULL DEFAULT '[]',
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
          created_by TEXT, modified_by TEXT);
        CREATE INDEX IF NOT EXISTS idx_inventory_sku ON inventory(sku);
        CREATE INDEX IF NOT EXISTS idx_inventory_name ON inventory(item_name, model_name);
        CREATE TABLE IF NOT EXISTS audit_log(
          id INTEGER PRIMARY KEY, user_id INTEGER, username TEXT, entity_type TEXT NOT NULL,
          entity_id INTEGER, action TEXT NOT NULL, old_value TEXT, new_value TEXT,
          created_at TEXT NOT NULL, created_by TEXT);
        CREATE TABLE IF NOT EXISTS taxonomy_categories(
          gid TEXT PRIMARY KEY, name TEXT NOT NULL, full_name TEXT NOT NULL);
        """)

        row = c.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()
        if row["n"] == 0:
            c.execute("INSERT INTO schema_version(version) VALUES (1)")
        _ver_row = c.execute("SELECT version FROM schema_version").fetchone()
        prev_version = _ver_row["version"] if _ver_row else 0

        # ── Non-destructive column migrations ────────────────────────────────
        inv_cols = {r["name"] for r in c.execute("PRAGMA table_info(inventory)")}

        # Rename ost → osp
        if "ost" in inv_cols and "osp" not in inv_cols:
            c.execute("ALTER TABLE inventory RENAME COLUMN ost TO osp")
            inv_cols.discard("ost")
            inv_cols.add("osp")

        # Core inventory columns that may not exist in older databases
        for col, defn in [
            ("lot_number",             "TEXT"),
            ("source_url",             "TEXT"),
            ("colours_json",           "TEXT NOT NULL DEFAULT '[]'"),
            ("updated_at",             "TEXT NOT NULL DEFAULT ''"),
            ("osp",                    "REAL"),
            ("capacity",               "INTEGER"),
            ("capacity_unit",          "TEXT"),
            ("amazon_asin",            "TEXT"),
            ("amazon_title",           "TEXT"),
            ("amazon_price",           "TEXT"),
            ("amazon_rating",          "TEXT"),
            ("amazon_reviews",         "TEXT"),
            ("amazon_availability",    "TEXT"),
            ("description",            "TEXT"),
            ("amazon_about_this_item", "TEXT"),
            ("specifications_json",    "TEXT NOT NULL DEFAULT '{}'"),
            ("images_json",            "TEXT NOT NULL DEFAULT '[]'"),
            ("sub_category_id",        "INTEGER REFERENCES sub_categories(id)"),
            ("created_by",             "TEXT"),
            ("modified_by",            "TEXT"),
            ("whats_in_the_box",       "TEXT"),
            ("weight_grams",           "REAL"),
            ("weight_unit",            "TEXT"),
            ("packed_length",          "REAL"),
            ("packed_width",           "REAL"),
            ("packed_height",          "REAL"),
            ("shopify_status",         "TEXT"),
            ("shopify_pushed_at",      "TEXT"),
            ("shopify_synced_qty",     "INTEGER NOT NULL DEFAULT 0"),
            ("variant_image",          "TEXT"),
            ("product_handle",         "TEXT"),
            ("hs_code",                "TEXT"),
            ("warehouse_location",     "TEXT"),
            ("shopify_category",       "TEXT"),
            ("shopify_category_id",    "TEXT"),
        ]:
            if col not in inv_cols:
                c.execute(f"ALTER TABLE inventory ADD COLUMN {col} {defn}")

        # Add created_by / modified_by to all other tables
        for table in ("users", "categories", "sub_categories", "brands", "grades", "colours", "abbreviations"):
            existing = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
            for col in ("created_by", "modified_by"):
                if col not in existing:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")

        # Add created_by to audit_log
        al_cols = {r["name"] for r in c.execute("PRAGMA table_info(audit_log)")}
        if "created_by" not in al_cols:
            c.execute("ALTER TABLE audit_log ADD COLUMN created_by TEXT")

        # Backfill NULL codes for master data that pre-dates the mandatory-code rule
        c.execute("UPDATE categories SET code=UPPER(SUBSTR(REPLACE(name,' ',''),1,8)) WHERE code IS NULL OR code=''")
        c.execute("UPDATE brands    SET code=UPPER(SUBSTR(REPLACE(name,' ',''),1,8)) WHERE code IS NULL OR code=''")
        c.execute("UPDATE grades    SET code=UPPER(SUBSTR(REPLACE(name,' ',''),1,8)) WHERE code IS NULL OR code=''")

        # ── One-time data backfill (v5): only when upgrading from an older schema ──
        if prev_version < 5:
            # Rows already synced before delta-tracking existed have their full
            # quantity on Shopify — mark it accounted so the next push doesn't
            # re-add it as a delta.
            c.execute("UPDATE inventory SET shopify_synced_qty=quantity "
                      "WHERE shopify_status='synced' AND shopify_synced_qty=0 AND quantity>0")

        # ── v6: rename single-letter grades to full names (Fresh unchanged) ──────
        if prev_version < 6:
            for _short, _full in (("A", "Grade A"), ("B", "Grade B")):
                c.execute(
                    "UPDATE grades SET name=? WHERE name=? COLLATE NOCASE "
                    "AND NOT EXISTS (SELECT 1 FROM grades WHERE name=? COLLATE NOCASE)",
                    (_full, _short, _full))

        c.execute("UPDATE schema_version SET version=6")

    # One-time backfills for legacy rows — only on upgrade, not every boot.
    if prev_version < 5:
        backfill_product_handles()
        backfill_physical_from_specs()


def backfill_physical_from_specs():
    """Fill weight / packed dimensions from the specifications JSON for rows
    that are missing them — covers records created before the parser handled
    formats like '58D x 55W x 85H Centimeters'."""
    with get_conn() as c:
        rows = c.execute(
            "SELECT id, specifications_json, weight_grams, packed_length "
            "FROM inventory"
        ).fetchall()
        for r in rows:
            try:
                specs = json.loads(r["specifications_json"] or "{}")
            except Exception:
                specs = {}
            phys = extract_physical_from_specs(specs)
            sets, params = [], []
            if r["weight_grams"] is None and phys["weight_grams"] is not None:
                sets += ["weight_grams=?", "weight_unit=?"]
                params += [phys["weight_grams"], phys["weight_unit"]]
            if r["packed_length"] is None and phys["packed_length"] is not None:
                sets += ["packed_length=?", "packed_width=?", "packed_height=?"]
                params += [phys["packed_length"], phys["packed_width"], phys["packed_height"]]
            if sets:
                params.append(r["id"])
                c.execute(f"UPDATE inventory SET {', '.join(sets)} WHERE id=?", params)


def backfill_product_handles():
    """Compute product_handle for any inventory rows missing one.

    The handle is the SKU prefix (CATEGORY-BRAND-MODEL) shared by every variant
    of the same product, so older records get a stable product id retroactively.
    """
    with get_conn() as c:
        rows = c.execute(
            "SELECT id, item_name, model_name, category_id, sub_category_id, brand_id "
            "FROM inventory WHERE product_handle IS NULL OR product_handle=''"
        ).fetchall()
        if not rows:
            return
        abbr = [dict(r) for r in c.execute(
            "SELECT name,code,priority FROM abbreviations WHERE active=1 ORDER BY priority DESC, length(name) DESC"
        )]
        cat_codes = {r["id"]: r["code"] for r in c.execute("SELECT id, code FROM categories")}
        sub_codes = {r["id"]: r["code"] for r in c.execute("SELECT id, code FROM sub_categories")}
        brand_codes = {r["id"]: r["code"] for r in c.execute("SELECT id, code FROM brands")}
        for r in rows:
            cat_code = sub_codes.get(r["sub_category_id"]) or cat_codes.get(r["category_id"])
            brand_code = brand_codes.get(r["brand_id"])
            if not cat_code or not brand_code:
                continue
            try:
                handle = generate_product_handle(
                    cat_code, brand_code, r["item_name"], r["model_name"] or "", abbr)
            except ValueError:
                continue
            c.execute("UPDATE inventory SET product_handle=? WHERE id=?", (handle, r["id"]))


# ── Auth ──────────────────────────────────────────────────────────────────────

def _hash_password(password):
    if len(password) < 10:
        raise ValueError("Password must be at least 10 characters.")
    salt = os.urandom(16)
    digest = scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=64)
    return "scrypt$" + salt.hex() + "$" + digest.hex()

def _verify_password(password, stored):
    try:
        algo, salt, expected = stored.split("$")
        if algo != "scrypt": return False
        got = scrypt(password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=64)
        return hmac.compare_digest(got.hex(), expected)
    except Exception:
        return False

def _actor_name(actor):
    return actor.get("username") if actor else "system"

def _audit(c, actor, entity_type, entity_id, action, old=None, new=None):
    c.execute(
        "INSERT INTO audit_log(user_id,username,entity_type,entity_id,action,old_value,new_value,created_at,created_by) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        (actor.get("id") if actor else None,
         _actor_name(actor), entity_type, entity_id, action,
         json.dumps(old, default=str) if old is not None else None,
         json.dumps(new, default=str) if new is not None else None,
         now(), _actor_name(actor)))

def admin_count():
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]

def create_initial_admin(username, password):
    with get_conn() as c:
        if c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
            raise ValueError("An administrator already exists.")
        t = now()
        c.execute(
            "INSERT INTO users(username,password_hash,role,active,created_at,updated_at,created_by) VALUES(?,?,'admin',1,?,?,?)",
            (username.strip(), _hash_password(password), t, t, "system"))
        _audit(c, {"id": None, "username": username.strip()}, "user",
               c.execute("SELECT last_insert_rowid()").fetchone()[0],
               "create_admin", new={"username": username.strip(), "role": "admin"})

def authenticate(username, password):
    with get_conn() as c:
        row = c.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE AND active=1", (username,)).fetchone()
        if not row or not _verify_password(password, row["password_hash"]):
            return None
        return {"id": row["id"], "username": row["username"], "role": row["role"]}

def create_user(username, password, role, actor):
    if not username or not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
        raise ValueError("Username must be 3–40 characters (letters, numbers, dot, underscore, hyphen).")
    if role not in ("admin", "standard"): raise ValueError("Invalid role.")
    with get_conn() as c:
        t = now()
        cur = c.execute(
            "INSERT INTO users(username,password_hash,role,active,created_at,updated_at,created_by) VALUES(?,?,?,1,?,?,?)",
            (username, _hash_password(password), role, t, t, _actor_name(actor)))
        _audit(c, actor, "user", cur.lastrowid, "create", new={"username": username, "role": role})

def list_users():
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT id,username,role,active,created_at,updated_at,created_by,modified_by FROM users ORDER BY username")]

def set_user_active(user_id, active, actor):
    with get_conn() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not row: raise ValueError("User not found.")
        if not active and row["role"] == "admin":
            n = c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
            if n <= 1: raise ValueError("Cannot deactivate the last active administrator.")
        c.execute("UPDATE users SET active=?,updated_at=?,modified_by=? WHERE id=?",
                  (int(active), now(), _actor_name(actor), user_id))
        _audit(c, actor, "user", user_id, "status_change",
               old={"active": row["active"]}, new={"active": int(active)})

def change_password(user_id, password):
    with get_conn() as c:
        c.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?",
                  (_hash_password(password), now(), user_id))


# ── Master data ───────────────────────────────────────────────────────────────

_MASTER_KINDS = ("categories", "brands", "grades", "colours", "abbreviations")

def list_master(kind, active_only=True):
    if kind not in _MASTER_KINDS: raise ValueError("Invalid master type.")
    with get_conn() as c:
        sql = (
            "SELECT id,name,code,active,created_at,updated_at,created_by,modified_by"
            + (",priority" if kind == "abbreviations" else "")
            + f" FROM {kind}"
        )
        if active_only: sql += " WHERE active=1"
        sql += " ORDER BY name"
        return [dict(r) for r in c.execute(sql)]

def list_sub_categories(category_id=None, active_only=True):
    with get_conn() as c:
        conditions, params = [], []
        if category_id is not None:
            conditions.append("s.parent_id = ?")
            params.append(int(category_id))
        if active_only:
            conditions.append("s.active = 1")
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        sql = (
            "SELECT s.id, s.name, s.code, s.parent_id, s.active, "
            "s.created_at, s.updated_at, s.created_by, s.modified_by, "
            "c.name AS category_name "
            "FROM sub_categories s JOIN categories c ON c.id = s.parent_id"
            + where + " ORDER BY s.name"
        )
        return [dict(r) for r in c.execute(sql, params)]

def add_master(kind, name, code, priority, actor, parent_id=None):
    if kind not in _MASTER_KINDS and kind != "sub_categories":
        raise ValueError("Invalid master type.")
    name = name.strip()
    if not name: raise ValueError("Name is required.")
    if not code or not code.strip(): raise ValueError("Code is required.")
    code = code.strip().upper()
    t = now()
    with get_conn() as c:
        if kind == "sub_categories":
            if not parent_id:
                raise ValueError("Parent category is required for sub-categories.")
            cur = c.execute(
                "INSERT INTO sub_categories(name,code,parent_id,active,created_at,updated_at,created_by) VALUES(?,?,?,1,?,?,?)",
                (name, code, int(parent_id), t, t, _actor_name(actor)))
        elif kind == "abbreviations":
            cur = c.execute(
                "INSERT INTO abbreviations(name,code,priority,active,created_at,updated_at,created_by) VALUES(?,?,?,1,?,?,?)",
                (name, code, int(priority), t, t, _actor_name(actor)))
        else:
            cur = c.execute(
                f"INSERT INTO {kind}(name,code,active,created_at,updated_at,created_by) VALUES(?,?,1,?,?,?)",
                (name, code, t, t, _actor_name(actor)))
        _audit(c, actor, kind, cur.lastrowid, "create", new={"name": name, "code": code})

def update_master(kind, row_id, name, code, active, actor, parent_id=None):
    if kind not in _MASTER_KINDS and kind != "sub_categories":
        raise ValueError("Invalid master type.")
    if not name: raise ValueError("Name is required.")
    if not code or not code.strip(): raise ValueError("Code is required.")
    code = code.strip().upper()
    with get_conn() as c:
        if kind == "sub_categories":
            old = c.execute("SELECT * FROM sub_categories WHERE id=?", (row_id,)).fetchone()
            if not old: raise ValueError("Record not found.")
            pid = int(parent_id) if parent_id else old["parent_id"]
            c.execute(
                "UPDATE sub_categories SET name=?,code=?,parent_id=?,active=?,updated_at=?,modified_by=? WHERE id=?",
                (name, code, pid, int(active), now(), _actor_name(actor), row_id))
        else:
            old = c.execute(f"SELECT * FROM {kind} WHERE id=?", (row_id,)).fetchone()
            if not old: raise ValueError("Record not found.")
            c.execute(
                f"UPDATE {kind} SET name=?,code=?,active=?,updated_at=?,modified_by=? WHERE id=?",
                (name, code, int(active), now(), _actor_name(actor), row_id))
        _audit(c, actor, kind, row_id, "update",
               old=dict(old), new={"name": name, "code": code, "active": active})

def deactivate_master(kind, row_id, actor):
    table = kind if kind in _MASTER_KINDS else "sub_categories"
    with get_conn() as c:
        c.execute(f"UPDATE {table} SET active=0,updated_at=?,modified_by=? WHERE id=?",
                  (now(), _actor_name(actor), row_id))
        _audit(c, actor, kind, row_id, "deactivate")

def _master(c, kind, row_id):
    return c.execute(f"SELECT * FROM {kind} WHERE id=? AND active=1", (row_id,)).fetchone()


# ── Inventory ─────────────────────────────────────────────────────────────────

def preview_variants(item_name, model_name, category_id, sub_category_id, brand_id, grade_id,
                     colour_names, quantity, actor):
    with get_conn() as c:
        cat     = _master(c, "categories", category_id)
        brand   = _master(c, "brands", brand_id)
        grade   = _master(c, "grades", grade_id) if grade_id else None
        sub_cat = None
        if sub_category_id:
            sub_cat = c.execute(
                "SELECT * FROM sub_categories WHERE id=? AND active=1", (sub_category_id,)
            ).fetchone()
        if not cat or not brand:
            raise ValueError("Select an active category and brand.")
        if grade_id and not grade:
            raise ValueError("Selected grade is inactive.")
        if not item_name.strip():
            raise ValueError("Item name is required.")

        colour_rows = []
        for cname in sorted(set(colour_names), key=lambda x: x.casefold()):
            r = c.execute("SELECT * FROM colours WHERE name=? COLLATE NOCASE AND active=1", (cname,)).fetchone()
            if not r: raise ValueError(f"Colour '{cname}' is not active.")
            colour_rows.append(dict(r))

        colour_rows.sort(key=lambda r: r["name"].casefold())
        selected_names = [r["name"] for r in colour_rows]
        combined_code  = "".join(re.sub(r"\s+", "", r["code"]).upper() for r in colour_rows)
        abbr = [dict(r) for r in c.execute(
            "SELECT name,code,priority FROM abbreviations WHERE active=1 ORDER BY priority DESC, length(name) DESC"
        )]

        sku_category_code = sub_cat["code"] if sub_cat else cat["code"]
        sku = generate_sku(sku_category_code, brand["code"], item_name, model_name,
                           grade["code"] if grade else "", combined_code, abbr)
        product_handle = generate_product_handle(sku_category_code, brand["code"],
                                                  item_name, model_name, abbr)

        existing = c.execute("SELECT * FROM inventory WHERE sku=? COLLATE NOCASE", (sku,)).fetchone()
        action   = "SKU exists — add to current stock" if existing else "New SKU — create inventory record"
        conflict = False
        if existing:
            existing_colours = json.loads(existing["colours_json"] or "[]")
            same = (
                existing["item_name"].casefold() == item_name.casefold()
                and (existing["model_name"] or "").casefold() == (model_name or "").casefold()
                and existing["category_id"] == category_id
                and existing["brand_id"]    == brand_id
                and existing["grade_id"]    == grade_id
                and existing["colour_code"] == combined_code
                and sorted(existing_colours, key=str.casefold) == sorted(selected_names, key=str.casefold)
            )
            if not same:
                conflict = True
                action = "BLOCKED — SKU attribute conflict"

        return [{
            "sku": sku,
            "product_handle": product_handle,
            "colours": ", ".join(selected_names) if selected_names else "—",
            "colour_code": combined_code or "—",
            "quantity_to_add": int(quantity),
            "current_quantity": int(existing["quantity"]) if existing else 0,
            "projected_quantity": int(existing["quantity"]) + int(quantity) if existing else int(quantity),
            "action": action,
            "conflict": conflict,
            "selected_colours": selected_names,
            "existing_id": int(existing["id"]) if existing else None,
        }]

def save_inventory_variants(draft, actor):
    messages = []
    with get_conn() as c:
        for v in draft["variants"]:
            if v["conflict"]:
                raise ValueError(f"SKU {v['sku']} conflicts with an existing record.")
            existing     = c.execute("SELECT * FROM inventory WHERE sku=? COLLATE NOCASE", (v["sku"],)).fetchone()
            cc_value     = v["colour_code"] if v["colour_code"] != "—" else ""
            colour_names = v.get("selected_colours", [])
            # Per-variant fields (fall back to draft for backward compatibility)
            v_grade_id  = v.get("grade_id",       draft.get("grade_id"))
            v_qty       = v.get("quantity_to_add", draft.get("quantity", 1))
            v_mrp       = v.get("mrp",             draft.get("mrp"))
            v_purchase  = v.get("purchase_price",  draft.get("purchase_price"))
            v_selling   = v.get("selling_price",   draft.get("selling_price"))
            v_lot       = v.get("lot_number",      draft.get("lot_number"))
            v_osp       = v.get("osp",             draft.get("osp"))
            if existing:
                existing_colours = json.loads(existing["colours_json"] or "[]")
                same = (
                    existing["item_name"].casefold() == draft["item_name"].casefold()
                    and (existing["model_name"] or "").casefold() == draft["model_name"].casefold()
                    and existing["category_id"] == draft["category_id"]
                    and existing["brand_id"]    == draft["brand_id"]
                    and existing["grade_id"]    == v_grade_id
                    and existing["colour_code"] == cc_value
                    and sorted(existing_colours, key=str.casefold) == sorted(colour_names, key=str.casefold)
                )
                if not same:
                    raise ValueError(f"SKU {v['sku']} now conflicts with a different product.")
                oldq = existing["quantity"]
                # A top-up on an already-synced variant flips only THAT variant to
                # "modified" so the product shows as needing a re-push. Variants not
                # yet synced keep their status (NULL/failed).
                new_status = "modified" if existing["shopify_status"] == "synced" else existing["shopify_status"]
                c.execute("UPDATE inventory SET quantity=quantity+?,shopify_status=?,updated_at=?,modified_by=? WHERE id=?",
                          (v_qty, new_status, now(), _actor_name(actor), existing["id"]))
                _audit(c, actor, "inventory", existing["id"], "quantity_increase",
                       old={"quantity": oldq, "shopify_status": existing["shopify_status"]},
                       new={"quantity": oldq + v_qty, "sku": v["sku"], "shopify_status": new_status})
                messages.append(f"{v['sku']}: quantity increased from {oldq} to {oldq + v_qty}.")
            else:
                t = now()
                cur = c.execute("""
                    INSERT INTO inventory(
                      item_name,model_name,category_id,sub_category_id,brand_id,grade_id,
                      colour_code,colours_json,quantity,mrp,purchase_price,selling_price,sku,
                      lot_number,source_url,osp,capacity,capacity_unit,
                      amazon_asin,amazon_title,amazon_price,amazon_rating,amazon_reviews,
                      amazon_availability,description,amazon_about_this_item,
                      specifications_json,images_json,variant_image,product_handle,
                      hs_code,warehouse_location,shopify_category,shopify_category_id,
                      whats_in_the_box,weight_grams,weight_unit,
                      packed_length,packed_width,packed_height,
                      created_at,updated_at,created_by)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (draft["item_name"], draft["model_name"],
                     draft["category_id"], draft.get("sub_category_id"), draft["brand_id"], v_grade_id,
                     cc_value, json.dumps(colour_names),
                     v_qty, v_mrp, v_purchase, v_selling,
                     v["sku"], v_lot, draft.get("source_url"),
                     v_osp, draft.get("capacity"), draft.get("capacity_unit"),
                     draft.get("amazon_asin"), draft.get("amazon_title"), draft.get("amazon_price"),
                     draft.get("amazon_rating"), draft.get("amazon_reviews"), draft.get("amazon_availability"),
                     draft.get("description"), draft.get("amazon_about_this_item"),
                     json.dumps(draft.get("specifications", {}), ensure_ascii=False),
                     json.dumps(draft.get("images", []), ensure_ascii=False),
                     v.get("variant_image") or "",
                     v.get("product_handle") or draft.get("product_handle"),
                     v.get("hs_code") or None, v.get("warehouse_location") or None,
                     draft.get("shopify_category") or None, draft.get("shopify_category_id") or None,
                     draft.get("whats_in_the_box"), draft.get("weight_grams"), draft.get("weight_unit"),
                     draft.get("packed_length"), draft.get("packed_width"), draft.get("packed_height"),
                     t, t, _actor_name(actor)))
                _audit(c, actor, "inventory", cur.lastrowid, "create",
                       new={"sku": v["sku"], "quantity": v_qty, "colours": colour_names})
                messages.append(f"{v['sku']}: created for {', '.join(colour_names) if colour_names else 'no colour'} "
                                 f"with quantity {v_qty}.")
    return messages

def list_inventory():
    with get_conn() as c:
        return [dict(r) for r in c.execute("""
            SELECT i.*, c.name AS category, sc.name AS sub_category, b.name AS brand, g.name AS grade
            FROM inventory i
            JOIN  categories c  ON c.id  = i.category_id
            LEFT JOIN sub_categories sc ON sc.id = i.sub_category_id
            JOIN  brands b      ON b.id  = i.brand_id
            LEFT JOIN grades g  ON g.id  = i.grade_id
            ORDER BY i.updated_at DESC""")]

def get_inventory_by_id(row_id):
    with get_conn() as c:
        row = c.execute("""
            SELECT i.*, c.name AS category, sc.name AS sub_category,
                   b.name AS brand, g.name AS grade
            FROM inventory i
            JOIN  categories c  ON c.id  = i.category_id
            LEFT JOIN sub_categories sc ON sc.id = i.sub_category_id
            JOIN  brands b      ON b.id  = i.brand_id
            LEFT JOIN grades g  ON g.id  = i.grade_id
            WHERE i.id = ?""", (row_id,)).fetchone()
        if not row: raise ValueError("Inventory record not found.")
        return dict(row)

def get_product_by_handle(product_handle):
    """Return the first existing inventory row for a product handle (product-level
    content is shared across its variants), or None if the product is new."""
    if not product_handle:
        return None
    with get_conn() as c:
        row = c.execute(
            "SELECT * FROM inventory WHERE product_handle = ? COLLATE NOCASE "
            "ORDER BY id LIMIT 1", (product_handle,)
        ).fetchone()
        return dict(row) if row else None


# ── Shopify taxonomy (synced leaf categories for the dropdown) ──────────────────

def replace_taxonomy(rows):
    """Replace the stored Shopify taxonomy leaves with a fresh sync."""
    with get_conn() as c:
        c.execute("DELETE FROM taxonomy_categories")
        c.executemany(
            "INSERT OR REPLACE INTO taxonomy_categories(gid,name,full_name) VALUES(?,?,?)",
            [(r["gid"], r["name"], r["full_name"]) for r in rows if r.get("gid")],
        )

def list_taxonomy_leaves():
    with get_conn() as c:
        return [dict(r) for r in c.execute(
            "SELECT gid,name,full_name FROM taxonomy_categories ORDER BY name COLLATE NOCASE")]

def taxonomy_count():
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM taxonomy_categories").fetchone()[0]

def update_inventory(row_id, changes, actor):
    with get_conn() as c:
        row = c.execute("SELECT * FROM inventory WHERE id=?", (row_id,)).fetchone()
        if not row: raise ValueError("Inventory record not found.")
        old  = dict(row)
        specs  = changes.get("specifications", json.loads(row["specifications_json"] or "{}"))
        images = changes.get("images",         json.loads(row["images_json"]         or "[]"))
        c.execute("""
            UPDATE inventory
            SET item_name=?,model_name=?,quantity=?,mrp=?,purchase_price=?,selling_price=?,
                lot_number=?,source_url=?,osp=?,capacity=?,capacity_unit=?,
                amazon_asin=?,amazon_title=?,amazon_price=?,amazon_rating=?,amazon_reviews=?,
                amazon_availability=?,description=?,amazon_about_this_item=?,
                specifications_json=?,images_json=?,variant_image=?,
                whats_in_the_box=?,weight_grams=?,weight_unit=?,
                packed_length=?,packed_width=?,packed_height=?,
                updated_at=?,modified_by=?
            WHERE id=?""",
            (changes["item_name"], changes["model_name"],
             changes["quantity"], changes["mrp"], changes["purchase_price"], changes["selling_price"],
             changes.get("lot_number",  row["lot_number"]),
             changes.get("source_url",  row["source_url"]),
             changes.get("osp",         row["osp"]),
             changes.get("capacity",    row["capacity"]),
             changes.get("capacity_unit", row["capacity_unit"]),
             changes.get("amazon_asin",  row["amazon_asin"]),
             changes.get("amazon_title", row["amazon_title"]),
             changes.get("amazon_price", row["amazon_price"]),
             changes.get("amazon_rating", row["amazon_rating"]),
             changes.get("amazon_reviews", row["amazon_reviews"]),
             changes.get("amazon_availability", row["amazon_availability"]),
             changes.get("description",            row["description"]),
             changes.get("amazon_about_this_item", row["amazon_about_this_item"]),
             json.dumps(specs,   ensure_ascii=False),
             json.dumps(images,  ensure_ascii=False),
             changes.get("variant_image", row.get("variant_image", "")),
             changes.get("whats_in_the_box", row.get("whats_in_the_box")),
             changes.get("weight_grams",     row.get("weight_grams")),
             changes.get("weight_unit",      row.get("weight_unit")),
             changes.get("packed_length",    row.get("packed_length")),
             changes.get("packed_width",     row.get("packed_width")),
             changes.get("packed_height",    row.get("packed_height")),
             now(), _actor_name(actor), row_id))
        _audit(c, actor, "inventory", row_id, "update", old=old, new=changes)


def update_product_content(row_ids, changes, actor):
    """Update product-level content shared across every variant of a product.

    Description, About this item, What's inside the box, Specifications and the
    source URL are the same for all variants, so they are written to every
    inventory row that belongs to the product.
    """
    if not row_ids:
        return
    # When specifications are edited, re-derive weight & packed dimensions from them.
    phys = {}
    if changes.get("specifications") is not None:
        phys = extract_physical_from_specs(changes["specifications"])
    with get_conn() as c:
        for rid in row_ids:
            row = c.execute("SELECT * FROM inventory WHERE id=?", (rid,)).fetchone()
            if not row:
                continue
            specs = changes.get("specifications")
            specs_json = (json.dumps(specs, ensure_ascii=False)
                          if specs is not None else row["specifications_json"])

            def _phys_or_row(key):
                if changes.get(key) is not None:
                    return changes[key]
                if phys.get(key) is not None:
                    return phys[key]
                return row[key]

            c.execute("""
                UPDATE inventory
                SET model_name=?, capacity=?, capacity_unit=?,
                    description=?, amazon_about_this_item=?, whats_in_the_box=?,
                    specifications_json=?, source_url=?, shopify_category=?,
                    weight_grams=?, weight_unit=?,
                    packed_length=?, packed_width=?, packed_height=?,
                    updated_at=?, modified_by=?
                WHERE id=?""",
                (changes.get("model_name",             row["model_name"]),
                 changes.get("capacity",               row["capacity"]),
                 changes.get("capacity_unit",          row["capacity_unit"]),
                 changes.get("description",            row["description"]),
                 changes.get("amazon_about_this_item", row["amazon_about_this_item"]),
                 changes.get("whats_in_the_box",       row["whats_in_the_box"]),
                 specs_json,
                 changes.get("source_url",             row["source_url"]),
                 changes.get("shopify_category",        row["shopify_category"]),
                 _phys_or_row("weight_grams"),  _phys_or_row("weight_unit"),
                 _phys_or_row("packed_length"), _phys_or_row("packed_width"),
                 _phys_or_row("packed_height"),
                 now(), _actor_name(actor), rid))
            _audit(c, actor, "inventory", rid, "update_content", new=changes)


# ── Audit ─────────────────────────────────────────────────────────────────────

def audit_rows(limit=100, offset=0):
    with get_conn() as c:
        total = c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0]
        rows  = [dict(r) for r in c.execute(
            "SELECT id,username,entity_type,entity_id,action,old_value,new_value,created_at,created_by "
            "FROM audit_log ORDER BY id DESC LIMIT ? OFFSET ?",
            (limit, offset)
        )]
        return rows, total


# ── Bulk import ───────────────────────────────────────────────────────────────

def bulk_add_master(kind, records, actor):
    if kind not in _MASTER_KINDS:
        raise ValueError("Bulk import is not supported for sub-categories (use the form instead).")
    results = []
    seen_names, seen_codes = set(), set()
    with get_conn() as c:
        for row_num, record in enumerate(records, start=2):
            name = str(record.get("name", "") or "").strip()
            code = str(record.get("code", "") or "").strip().upper()
            if not name or not code:
                results.append({"row": row_num, "name": name, "status": "Skipped",
                                 "detail": "Both name and code are required."})
                continue
            nk, ck = name.casefold(), code.casefold()
            if nk in seen_names:
                results.append({"row": row_num, "name": name, "status": "Skipped",
                                 "detail": "Duplicate name within CSV."})
                continue
            if ck in seen_codes:
                results.append({"row": row_num, "name": name, "status": "Skipped",
                                 "detail": "Duplicate code within CSV."})
                continue
            seen_names.add(nk); seen_codes.add(ck)
            if c.execute(f"SELECT 1 FROM {kind} WHERE name=? COLLATE NOCASE", (name,)).fetchone():
                results.append({"row": row_num, "name": name, "status": "Skipped",
                                 "detail": "Name already exists."})
                continue
            if kind != "abbreviations" and c.execute(f"SELECT 1 FROM {kind} WHERE code=? COLLATE NOCASE", (code,)).fetchone():
                results.append({"row": row_num, "name": name, "status": "Skipped",
                                 "detail": "Code already exists."})
                continue
            t = now()
            if kind == "abbreviations":
                cur = c.execute(
                    "INSERT INTO abbreviations(name,code,priority,active,created_at,updated_at,created_by) VALUES(?,?,0,1,?,?,?)",
                    (name, code, t, t, _actor_name(actor)))
            else:
                cur = c.execute(
                    f"INSERT INTO {kind}(name,code,active,created_at,updated_at,created_by) VALUES(?,?,1,?,?,?)",
                    (name, code, t, t, _actor_name(actor)))
            _audit(c, actor, kind, cur.lastrowid, "bulk_create", new={"name": name, "code": code})
            results.append({"row": row_num, "name": name, "status": "Imported", "detail": "Added successfully."})
    return results


def bulk_add_sub_categories(records, actor):
    """Bulk-insert sub-categories from CSV rows {parent, name, code}.

    Rows whose parent category name doesn't exist in the categories table
    are skipped with a clear reason — no silent data loss.
    """
    results = []
    seen = set()  # (parent_casefold, name_casefold) pairs seen so far in this CSV

    with get_conn() as c:
        # Build a lookup: category name (casefold) → id, using only active categories
        cat_map = {
            row["name"].casefold(): row["id"]
            for row in c.execute("SELECT id, name FROM categories WHERE active=1").fetchall()
        }

        for row_num, record in enumerate(records, start=2):
            parent = str(record.get("parent", "") or "").strip()
            name   = str(record.get("name",   "") or "").strip()
            code   = str(record.get("code",   "") or "").strip().upper()

            if not parent or not name or not code:
                results.append({"row": row_num, "parent": parent, "name": name,
                                 "status": "Skipped", "detail": "parent, name and code are all required."})
                continue

            parent_id = cat_map.get(parent.casefold())
            if parent_id is None:
                results.append({"row": row_num, "parent": parent, "name": name,
                                 "status": "Skipped", "detail": f"Parent category '{parent}' not found in categories table."})
                continue

            key = (parent.casefold(), name.casefold())
            if key in seen:
                results.append({"row": row_num, "parent": parent, "name": name,
                                 "status": "Skipped", "detail": "Duplicate (parent + name) within CSV."})
                continue
            seen.add(key)

            if c.execute("SELECT 1 FROM sub_categories WHERE name=? COLLATE NOCASE AND parent_id=?",
                         (name, parent_id)).fetchone():
                results.append({"row": row_num, "parent": parent, "name": name,
                                 "status": "Skipped", "detail": "Sub-category already exists under this parent."})
                continue

            if c.execute("SELECT 1 FROM sub_categories WHERE code=? COLLATE NOCASE",
                         (code,)).fetchone():
                results.append({"row": row_num, "parent": parent, "name": name,
                                 "status": "Skipped", "detail": f"Code '{code}' already exists."})
                continue

            t   = now()
            cur = c.execute(
                "INSERT INTO sub_categories(parent_id,name,code,active,created_at,updated_at,created_by) "
                "VALUES(?,?,?,1,?,?,?)",
                (parent_id, name, code, t, t, _actor_name(actor)),
            )
            _audit(c, actor, "sub_categories", cur.lastrowid, "bulk_create",
                   new={"parent": parent, "name": name, "code": code})
            results.append({"row": row_num, "parent": parent, "name": name,
                             "status": "Imported", "detail": "Added successfully."})

    return results


# ── Suggestions ───────────────────────────────────────────────────────────────

def inventory_suggestions(field, query=""):
    """Return distinct item/model names for autocomplete.
    When query is empty, returns all values (for client-side filtering).
    When query is provided, filters server-side."""
    if field not in ("item_name", "model_name"):
        raise ValueError("Unsupported suggestion field")
    with get_conn() as c:
        q = (query or "").strip()
        if q:
            return [r[0] for r in c.execute(
                f"SELECT DISTINCT {field} FROM inventory "
                f"WHERE TRIM(COALESCE({field},'')) <> '' AND {field} LIKE ? COLLATE NOCASE "
                f"ORDER BY {field} COLLATE NOCASE LIMIT 10",
                (f"%{q}%",)
            ).fetchall()]
        return [r[0] for r in c.execute(
            f"SELECT DISTINCT {field} FROM inventory "
            f"WHERE TRIM(COALESCE({field},'')) <> '' "
            f"ORDER BY {field} COLLATE NOCASE LIMIT 200"
        ).fetchall()]

def sku_exists(sku):
    with get_conn() as c:
        return c.execute(
            "SELECT 1 FROM inventory WHERE sku=? COLLATE NOCASE", (sku,)
        ).fetchone() is not None


def update_shopify_status(row_ids: list, status: str):
    """Set the Shopify status for the given rows (used for 'failed'). Successful
    syncs go through mark_synced(), which also advances shopify_synced_qty."""
    if not row_ids:
        return
    with get_conn() as c:
        c.executemany(
            "UPDATE inventory SET shopify_status=?, shopify_pushed_at=? WHERE id=?",
            [(status, now(), rid) for rid in row_ids],
        )


def mark_synced(id_to_qty: dict):
    """Mark rows synced and advance shopify_synced_qty to the quantity that was
    actually pushed (snapshotted at push time), so future top-ups push only the
    new delta. Only call for variants whose Shopify inventory actually applied."""
    if not id_to_qty:
        return
    with get_conn() as c:
        c.executemany(
            "UPDATE inventory SET shopify_status='synced', shopify_pushed_at=?, "
            "shopify_synced_qty=? WHERE id=?",
            [(now(), int(q), int(rid)) for rid, q in id_to_qty.items()],
        )
