import sqlite3, os, json, re
from pathlib import Path
from datetime import datetime, timezone
from hashlib import scrypt, sha256
import hmac
from sku import generate_sku, colour_code

DB_PATH = Path(os.environ.get("INVENTORY_DB_PATH", Path(__file__).with_name("inventory.db")))

def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

def get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def init_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS schema_version(version INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS users(
          id INTEGER PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
          password_hash TEXT NOT NULL, role TEXT NOT NULL CHECK(role IN ('admin','standard')),
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS categories(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, code TEXT UNIQUE COLLATE NOCASE,
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS brands(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, code TEXT UNIQUE COLLATE NOCASE,
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS grades(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, code TEXT UNIQUE COLLATE NOCASE,
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS colours(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, code TEXT NOT NULL UNIQUE COLLATE NOCASE,
          active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS abbreviations(
          id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE COLLATE NOCASE, code TEXT NOT NULL,
          priority INTEGER NOT NULL DEFAULT 0, active INTEGER NOT NULL DEFAULT 1,
          created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS inventory(
          id INTEGER PRIMARY KEY, item_name TEXT NOT NULL, model_name TEXT NOT NULL DEFAULT '',
          category_id INTEGER NOT NULL REFERENCES categories(id), brand_id INTEGER NOT NULL REFERENCES brands(id),
          grade_id INTEGER REFERENCES grades(id), colour_code TEXT NOT NULL DEFAULT '',
          colours_json TEXT NOT NULL DEFAULT '[]', quantity INTEGER NOT NULL DEFAULT 0 CHECK(quantity>=0),
          mrp REAL, purchase_price REAL, selling_price REAL, sku TEXT NOT NULL UNIQUE COLLATE NOCASE,
          lot_number TEXT, source_url TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_inventory_sku ON inventory(sku);
        CREATE INDEX IF NOT EXISTS idx_inventory_name ON inventory(item_name, model_name);
        CREATE TABLE IF NOT EXISTS audit_log(
          id INTEGER PRIMARY KEY, user_id INTEGER, username TEXT, entity_type TEXT NOT NULL,
          entity_id INTEGER, action TEXT NOT NULL, old_value TEXT, new_value TEXT, created_at TEXT NOT NULL);
        """)
        row = c.execute("SELECT COUNT(*) AS n FROM schema_version").fetchone()
        if row["n"] == 0:
            c.execute("INSERT INTO schema_version(version) VALUES (1)")
        # Non-destructive compatibility migration for older versions.
        cols = {r["name"] for r in c.execute("PRAGMA table_info(inventory)")}
        if "lot_number" not in cols:
            c.execute("ALTER TABLE inventory ADD COLUMN lot_number TEXT")
        if "source_url" not in cols:
            c.execute("ALTER TABLE inventory ADD COLUMN source_url TEXT")
        if "colours_json" not in cols:
            c.execute("ALTER TABLE inventory ADD COLUMN colours_json TEXT NOT NULL DEFAULT '[]'")
        if "updated_at" not in cols:
            c.execute("ALTER TABLE inventory ADD COLUMN updated_at TEXT NOT NULL DEFAULT ''")
        c.execute("UPDATE schema_version SET version=1")

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

def _audit(c, actor, entity_type, entity_id, action, old=None, new=None):
    c.execute("INSERT INTO audit_log(user_id,username,entity_type,entity_id,action,old_value,new_value,created_at) VALUES(?,?,?,?,?,?,?,?)",
        (actor.get("id") if actor else None, actor.get("username") if actor else "system", entity_type, entity_id, action,
         json.dumps(old, default=str) if old is not None else None, json.dumps(new, default=str) if new is not None else None, now()))

def admin_count():
    with get_conn() as c:
        return c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]

def create_initial_admin(username, password):
    with get_conn() as c:
        if c.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
            raise ValueError("An administrator already exists.")
        t = now()
        c.execute("INSERT INTO users(username,password_hash,role,active,created_at,updated_at) VALUES(?,?, 'admin',1,?,?)",
                  (username.strip(), _hash_password(password), t, t))
        _audit(c, {"id": None, "username": username.strip()}, "user", c.execute("SELECT last_insert_rowid()").fetchone()[0], "create_admin",
               new={"username": username.strip(), "role": "admin"})

def authenticate(username, password):
    with get_conn() as c:
        row = c.execute("SELECT * FROM users WHERE username=? COLLATE NOCASE AND active=1", (username,)).fetchone()
        if not row or not _verify_password(password, row["password_hash"]):
            return None
        return {"id": row["id"], "username": row["username"], "role": row["role"]}

def create_user(username, password, role, actor):
    if not username or not re.fullmatch(r"[A-Za-z0-9_.-]{3,40}", username):
        raise ValueError("Username must be 3–40 characters and use letters, numbers, dot, underscore or hyphen.")
    if role not in ("admin", "standard"): raise ValueError("Invalid role.")
    with get_conn() as c:
        t = now()
        cur = c.execute("INSERT INTO users(username,password_hash,role,active,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (username, _hash_password(password), role, 1, t, t))
        _audit(c, actor, "user", cur.lastrowid, "create", new={"username": username, "role": role, "active": True})

def list_users():
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT id,username,role,active,created_at,updated_at FROM users ORDER BY username")]

def set_user_active(user_id, active, actor):
    with get_conn() as c:
        row = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not row: raise ValueError("User not found.")
        if not active and row["role"] == "admin":
            n = c.execute("SELECT COUNT(*) FROM users WHERE role='admin' AND active=1").fetchone()[0]
            if n <= 1: raise ValueError("Cannot deactivate the last active administrator.")
        c.execute("UPDATE users SET active=?,updated_at=? WHERE id=?", (int(active), now(), user_id))
        _audit(c, actor, "user", user_id, "status_change", old={"active": row["active"]}, new={"active": int(active)})

def change_password(user_id, password):
    with get_conn() as c:
        c.execute("UPDATE users SET password_hash=?,updated_at=? WHERE id=?", (_hash_password(password), now(), user_id))

def list_master(kind, active_only=True):
    if kind not in ("categories","brands","grades","colours","abbreviations"): raise ValueError("Invalid master type.")
    with get_conn() as c:
        sql = f"SELECT id,name,code,active,created_at,updated_at" + (",priority" if kind == "abbreviations" else "") + f" FROM {kind}"
        if active_only: sql += " WHERE active=1"
        sql += " ORDER BY name"
        return [dict(r) for r in c.execute(sql)]

def add_master(kind, name, code, priority, actor):
    if kind not in ("categories","brands","grades","colours","abbreviations"): raise ValueError("Invalid master type.")
    name = name.strip()
    if not name: raise ValueError("Name is required.")
    if kind == "colours" and not code: code = re.sub(r"[^A-Z0-9]", "", name.upper())
    if kind != "abbreviations" and not code: code = re.sub(r"[^A-Z0-9]", "", name.upper())[:8]
    if not code: raise ValueError("Code / abbreviation is required.")
    t = now()
    with get_conn() as c:
        cur = c.execute(f"INSERT INTO {kind}(name,code,active,created_at,updated_at" +
            (",priority" if kind == "abbreviations" else "") + ") VALUES(?,?,1,?,?" +
            (",?" if kind == "abbreviations" else "") + ")",
            (name, code.upper(), t, t, priority) if kind == "abbreviations" else (name, code.upper(), t, t))
        _audit(c, actor, kind, cur.lastrowid, "create", new={"name":name,"code":code.upper()})

def update_master(kind, row_id, name, code, active, actor):
    if kind not in ("categories","brands","grades","colours","abbreviations"): raise ValueError("Invalid master type.")
    if not name: raise ValueError("Name is required.")
    if kind == "colours" and not code: raise ValueError("Colour code is required.")
    with get_conn() as c:
        old = c.execute(f"SELECT * FROM {kind} WHERE id=?", (row_id,)).fetchone()
        if not old: raise ValueError("Record not found.")
        c.execute(f"UPDATE {kind} SET name=?,code=?,active=?,updated_at=? WHERE id=?",
                  (name, code.upper() if code else None, int(active), now(), row_id))
        _audit(c, actor, kind, row_id, "update", old=dict(old), new={"name":name,"code":code,"active":active})

def deactivate_master(kind, row_id, actor):
    with get_conn() as c:
        c.execute(f"UPDATE {kind} SET active=0,updated_at=? WHERE id=?", (now(), row_id))
        _audit(c, actor, kind, row_id, "deactivate")

def _master(c, kind, row_id):
    return c.execute(f"SELECT * FROM {kind} WHERE id=? AND active=1", (row_id,)).fetchone()

def preview_variants(item_name, model_name, category_id, brand_id, grade_id, colour_names, quantity, actor):
    with get_conn() as c:
        cat, brand = _master(c, "categories", category_id), _master(c, "brands", brand_id)
        grade = _master(c, "grades", grade_id) if grade_id else None
        if not cat or not brand:
            raise ValueError("Select an active category and brand.")
        if grade_id and not grade:
            raise ValueError("Selected grade is inactive.")
        if not item_name.strip():
            raise ValueError("Item name is required.")

        colour_rows = []
        for name in sorted(set(colour_names), key=lambda x: x.casefold()):
            r = c.execute("SELECT * FROM colours WHERE name=? COLLATE NOCASE AND active=1", (name,)).fetchone()
            if not r:
                raise ValueError(f"Colour '{name}' is not active.")
            colour_rows.append(dict(r))

        # One product entry = one SKU. All selected colours are sorted by colour
        # name and their configured codes are concatenated into one colour segment.
        colour_rows.sort(key=lambda r: r["name"].casefold())
        selected_names = [r["name"] for r in colour_rows]
        combined_code = "".join(re.sub(r"\\s+", "", r["code"]).upper() for r in colour_rows)
        abbr = [dict(r) for r in c.execute(
            "SELECT name,code,priority FROM abbreviations WHERE active=1 ORDER BY priority DESC, length(name) DESC"
        )]
        sku = generate_sku(
            cat["code"], brand["code"], item_name, model_name,
            grade["code"] if grade else "", combined_code, abbr
        )
        existing = c.execute("SELECT * FROM inventory WHERE sku=? COLLATE NOCASE", (sku,)).fetchone()
        action = "Increase existing quantity" if existing else "Create new inventory record"
        conflict = False
        if existing:
            existing_colours = json.loads(existing["colours_json"] or "[]")
            same = (
                existing["item_name"].casefold() == item_name.casefold()
                and (existing["model_name"] or "").casefold() == (model_name or "").casefold()
                and existing["category_id"] == category_id
                and existing["brand_id"] == brand_id
                and existing["grade_id"] == grade_id
                and existing["colour_code"] == combined_code
                and sorted(existing_colours, key=str.casefold) == sorted(selected_names, key=str.casefold)
            )
            if not same:
                conflict = True
                action = "BLOCKED — SKU attribute conflict"

        return [{
            "sku": sku,
            "colours": ", ".join(selected_names) if selected_names else "—",
            "colour_code": combined_code or "—",
            "quantity_to_add": int(quantity),
            "current_quantity": int(existing["quantity"]) if existing else 0,
            "projected_quantity": int(existing["quantity"]) + int(quantity) if existing else int(quantity),
            "action": action,
            "conflict": conflict,
            "selected_colours": selected_names,
        }]

def save_inventory_variants(draft, actor):
    messages = []
    with get_conn() as c:
        # Recheck SKU and attributes within the write transaction.
        for v in draft["variants"]:
            if v["conflict"]:
                raise ValueError(f"SKU {v['sku']} conflicts with an existing record. Ask an admin to resolve it.")
            existing = c.execute("SELECT * FROM inventory WHERE sku=? COLLATE NOCASE", (v["sku"],)).fetchone()
            colour_code_value = v["colour_code"] if v["colour_code"] != "—" else ""
            colour_names = v.get("selected_colours", [])
            if existing:
                existing_colours = json.loads(existing["colours_json"] or "[]")
                same = (
                    existing["item_name"].casefold() == draft["item_name"].casefold()
                    and (existing["model_name"] or "").casefold() == draft["model_name"].casefold()
                    and existing["category_id"] == draft["category_id"]
                    and existing["brand_id"] == draft["brand_id"]
                    and existing["grade_id"] == draft["grade_id"]
                    and existing["colour_code"] == colour_code_value
                    and sorted(existing_colours, key=str.casefold) == sorted(colour_names, key=str.casefold)
                )
                if not same:
                    raise ValueError(f"SKU {v['sku']} now conflicts with a different product.")
                oldq = existing["quantity"]
                c.execute("UPDATE inventory SET quantity=quantity+?, updated_at=? WHERE id=?",
                          (draft["quantity"], now(), existing["id"]))
                _audit(c, actor, "inventory", existing["id"], "quantity_increase",
                       old={"quantity": oldq}, new={"quantity": oldq + draft["quantity"], "sku": v["sku"]})
                messages.append(f"{v['sku']}: quantity increased from {oldq} to {oldq + draft['quantity']}.")
            else:
                t = now()
                cur = c.execute("""INSERT INTO inventory(item_name,model_name,category_id,brand_id,grade_id,colour_code,colours_json,
                    quantity,mrp,purchase_price,selling_price,sku,lot_number,source_url,created_at,updated_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (draft["item_name"], draft["model_name"], draft["category_id"], draft["brand_id"], draft["grade_id"],
                     colour_code_value, json.dumps(colour_names), draft["quantity"], draft["mrp"], draft["purchase_price"],
                     draft["selling_price"], v["sku"], draft.get("lot_number"), draft["source_url"], t, t))
                _audit(c, actor, "inventory", cur.lastrowid, "create",
                       new={"sku": v["sku"], "quantity": draft["quantity"], "colours": colour_names})
                messages.append(f"{v['sku']}: one inventory record created for {', '.join(colour_names) if colour_names else 'no colour'} with quantity {draft['quantity']}.")
    return messages

def list_inventory():
    with get_conn() as c:
        return [dict(r) for r in c.execute("""SELECT i.*,c.name AS category,b.name AS brand,g.name AS grade
            FROM inventory i JOIN categories c ON c.id=i.category_id JOIN brands b ON b.id=i.brand_id
            LEFT JOIN grades g ON g.id=i.grade_id ORDER BY i.updated_at DESC""")]

def get_inventory_by_id(row_id):
    with get_conn() as c:
        row = c.execute("""SELECT i.*,c.name AS category,b.name AS brand,g.name AS grade
            FROM inventory i JOIN categories c ON c.id=i.category_id JOIN brands b ON b.id=i.brand_id
            LEFT JOIN grades g ON g.id=i.grade_id WHERE i.id=?""", (row_id,)).fetchone()
        if not row: raise ValueError("Inventory record not found.")
        return dict(row)

def update_inventory(row_id, changes, actor):
    with get_conn() as c:
        row = c.execute("SELECT * FROM inventory WHERE id=?", (row_id,)).fetchone()
        if not row: raise ValueError("Inventory record not found.")
        old = dict(row)
        c.execute("""UPDATE inventory SET item_name=?,model_name=?,quantity=?,mrp=?,purchase_price=?,selling_price=?,lot_number=?,source_url=?,updated_at=? WHERE id=?""",
            (changes["item_name"],changes["model_name"],changes["quantity"],changes["mrp"],changes["purchase_price"],
             changes["selling_price"],changes.get("lot_number"),changes["source_url"],now(),row_id))
        _audit(c, actor, "inventory", row_id, "update", old=old, new=changes)

def audit_rows():
    with get_conn() as c:
        return [dict(r) for r in c.execute("SELECT id,username,entity_type,entity_id,action,old_value,new_value,created_at FROM audit_log ORDER BY id DESC LIMIT 1000")]


def bulk_add_master(kind, records, actor):
    """Import master-data rows. Each record must contain name and code.
    Duplicate names/codes are skipped case-insensitively; valid rows are committed
    in one transaction and each successful insert is audited.
    """
    if kind not in ("categories", "brands", "grades", "colours", "abbreviations"):
        raise ValueError("Invalid master type.")
    results = []
    seen_names, seen_codes = set(), set()
    with get_conn() as c:
        for row_num, record in enumerate(records, start=2):
            name = str(record.get("name", "") or "").strip()
            code = str(record.get("code", "") or "").strip().upper()
            if not name or not code:
                results.append({"row": row_num, "name": name, "status": "Skipped", "detail": "Both name and code are required."})
                continue
            name_key, code_key = name.casefold(), code.casefold()
            if name_key in seen_names:
                results.append({"row": row_num, "name": name, "status": "Skipped", "detail": "Duplicate name within CSV (case-insensitive)."})
                continue
            if code_key in seen_codes:
                results.append({"row": row_num, "name": name, "status": "Skipped", "detail": "Duplicate code within CSV (case-insensitive)."})
                continue
            seen_names.add(name_key)
            seen_codes.add(code_key)
            existing_name = c.execute(f"SELECT 1 FROM {kind} WHERE name=? COLLATE NOCASE", (name,)).fetchone()
            if existing_name:
                results.append({"row": row_num, "name": name, "status": "Skipped", "detail": "Name already exists (case-insensitive)."})
                continue
            # Codes are unique for all current master tables except abbreviations.
            existing_code = c.execute(f"SELECT 1 FROM {kind} WHERE code=? COLLATE NOCASE", (code,)).fetchone()
            if existing_code:
                results.append({"row": row_num, "name": name, "status": "Skipped", "detail": "Code already exists (case-insensitive)."})
                continue
            t = now()
            if kind == "abbreviations":
                cur = c.execute(
                    "INSERT INTO abbreviations(name,code,priority,active,created_at,updated_at) VALUES(?,?,0,1,?,?)",
                    (name, code, t, t))
            else:
                cur = c.execute(
                    f"INSERT INTO {kind}(name,code,active,created_at,updated_at) VALUES(?,?,1,?,?)",
                    (name, code, t, t))
            _audit(c, actor, kind, cur.lastrowid, "bulk_create", new={"name": name, "code": code})
            results.append({"row": row_num, "name": name, "status": "Imported", "detail": "Added successfully."})
    return results
