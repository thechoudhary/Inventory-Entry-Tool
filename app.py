import streamlit as st
import pandas as pd
from db import (
    init_db, get_conn, authenticate, create_user, list_users, set_user_active,
    list_master, add_master, bulk_add_master, update_master, list_inventory, preview_variants,
    save_inventory_variants, update_inventory, get_inventory_by_id, audit_rows,
    change_password, admin_count, deactivate_master
)
from sku import generate_sku, colour_code
from urllib.parse import urlparse

st.set_page_config(page_title="Inventory & SKU Manager", page_icon="📦", layout="wide")
init_db()

def rerun():
    st.rerun()

def logged_in():
    return bool(st.session_state.get("user"))

def is_admin():
    return logged_in() and st.session_state.user["role"] == "admin"

def require_admin():
    if not is_admin():
        st.error("You do not have permission to access this section.")
        st.stop()

def master_options(kind):
    return list_master(kind, active_only=True)

def option_labels(rows):
    return ["Select"] + [r["name"] for r in rows]

def selected_id(rows, selected_name):
    for r in rows:
        if r["name"] == selected_name:
            return r["id"]
    return None

if not logged_in():
    st.title("📦 Inventory Entry & SKU Management")
    st.caption("Sign in to manage product variants, stock and SKU rules.")
    with st.form("login"):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Log in", type="primary", use_container_width=True)
    if submitted:
        user = authenticate(username.strip(), password)
        if user:
            st.session_state.user = user
            rerun()
        else:
            st.error("Invalid username/password, or this account is inactive.")
    st.info("First run? Create the initial administrator from your terminal: `python setup_admin.py`")
    st.stop()

user = st.session_state.user
with st.sidebar:
    st.title("📦 Inventory")
    st.write(f"Signed in as **{user['username']}**")
    st.caption(f"Role: {user['role'].title()}")
    pages = ["Dashboard", "Inventory", "Add Inventory"]
    if is_admin():
        pages += ["Manage", "User Management", "Audit History"]
    page = st.radio("Navigation", pages, label_visibility="collapsed")
    if st.button("Log out", use_container_width=True):
        st.session_state.pop("user", None)
        rerun()

if page == "Dashboard":
    st.title("Dashboard")
    inv = list_inventory()
    df = pd.DataFrame(inv)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Inventory records", len(df))
    c2.metric("Total units", int(df["quantity"].sum()) if not df.empty else 0)
    c3.metric("Unique SKUs", df["sku"].nunique() if not df.empty else 0)
    c4.metric("Zero-stock records", int((df["quantity"] == 0).sum()) if not df.empty else 0)
    st.subheader("Recently updated inventory")
    if df.empty:
        st.info("No inventory yet. Use Add Inventory to create your first item.")
    else:
        st.dataframe(df.sort_values("updated_at", ascending=False).head(10), use_container_width=True, hide_index=True)

elif page == "Inventory":
    st.title("Inventory")
    inv = list_inventory()
    df = pd.DataFrame(inv)
    if df.empty:
        st.info("No inventory records found.")
    else:
        search = st.text_input("Search by SKU, item or model")
        cols = st.columns(4)
        category = cols[0].selectbox("Category", ["All"] + sorted(df["category"].dropna().unique().tolist()))
        brand = cols[1].selectbox("Brand", ["All"] + sorted(df["brand"].dropna().unique().tolist()))
        grade = cols[2].selectbox("Grade", ["All"] + sorted(df["grade"].dropna().unique().tolist()))
        colour = cols[3].selectbox("Colour code", ["All"] + sorted(df["colour_code"].dropna().unique().tolist()))
        filtered = df.copy()
        if search:
            mask = filtered[["sku", "item_name", "model_name"]].fillna("").astype(str).apply(
                lambda col: col.str.contains(search, case=False, regex=False)).any(axis=1)
            filtered = filtered[mask]
        for col, val in [("category", category), ("brand", brand), ("grade", grade), ("colour_code", colour)]:
            if val != "All":
                filtered = filtered[filtered[col] == val]
        st.dataframe(filtered, use_container_width=True, hide_index=True)
        if is_admin():
            st.subheader("Edit inventory record")
            choices = {f"{r['sku']} — {r['item_name']} (ID {r['id']})": r["id"] for r in inv}
            choice = st.selectbox("Select record", ["Select"] + list(choices.keys()))
            if choice != "Select":
                row = get_inventory_by_id(choices[choice])
                with st.form("edit_inventory"):
                    item_name = st.text_input("Item name", row["item_name"])
                    model_name = st.text_input("Model name", row["model_name"] or "")
                    quantity = st.number_input("Quantity", min_value=0, step=1, value=int(row["quantity"]))
                    mrp = st.number_input("MRP", min_value=0.0, value=float(row["mrp"] or 0), step=1.0)
                    purchase = st.number_input("Purchase price", min_value=0.0, value=float(row["purchase_price"] or 0), step=1.0)
                    selling = st.number_input("Selling price", min_value=0.0, value=float(row["selling_price"] or 0), step=1.0)
                    lot_number = st.text_input("LOT Number (optional)", row.get("lot_number") or "")
                    source_url = st.text_input("Source URL (optional)", row["source_url"] or "")
                    save = st.form_submit_button("Save changes", type="primary")
                if save:
                    if source_url and urlparse(source_url).scheme not in ("http", "https"):
                        st.error("Source URL must start with http:// or https://")
                    elif not item_name.strip():
                        st.error("Item name is required.")
                    else:
                        try:
                            update_inventory(row["id"], {"item_name": item_name.strip(), "model_name": model_name.strip(),
                                "quantity": int(quantity), "mrp": mrp or None, "purchase_price": purchase or None,
                                "selling_price": selling or None, "lot_number": lot_number.strip() or None,
                                "source_url": source_url.strip() or None}, user)
                            st.success("Inventory updated.")
                            rerun()
                        except Exception as e:
                            st.error(str(e))

elif page == "Add Inventory":
    st.title("Add Inventory")
    st.caption("One SKU is generated for the product, combining all selected colour codes. Existing equivalent SKUs receive additional quantity.")
    cats, brands, grades, colours = [master_options(k) for k in ("categories", "brands", "grades", "colours")]
    if not cats or not brands:
        st.warning("An administrator must add at least one active category and brand in Manage before inventory can be added.")
    with st.form("add_inventory"):
        item_name = st.text_input("Item name *")
        model_name = st.text_input("Model name")
        c1, c2 = st.columns(2)
        category_name = c1.selectbox("Category *", option_labels(cats))
        brand_name = c2.selectbox("Brand *", option_labels(brands))
        c3, c4 = st.columns(2)
        grade_name = c3.selectbox("Grade", option_labels(grades))
        selected_colours = c4.multiselect("Colours", [r["name"] for r in colours])
        quantity = st.number_input("Quantity to add for this product *", min_value=1, step=1, value=1)
        c5, c6, c7 = st.columns(3)
        mrp = c5.number_input("MRP", min_value=0.0, value=0.0, step=1.0)
        purchase = c6.number_input("Purchase price", min_value=0.0, value=0.0, step=1.0)
        selling = c7.number_input("Selling price", min_value=0.0, value=0.0, step=1.0)
        lot_number = st.text_input("LOT Number")
        source_url = st.text_input("Source URL (optional)")
        preview = st.form_submit_button("Preview SKUs")
    if preview:
        if not item_name.strip() or category_name == "Select" or brand_name == "Select":
            st.error("Item name, category and brand are required.")
        if not lot_number.strip():
            st.error("LOT Number is required.")
        elif source_url and urlparse(source_url).scheme not in ("http", "https"):
            st.error("Source URL must start with http:// or https://")
        else:
            try:
                variants = preview_variants(item_name.strip(), model_name.strip(), selected_id(cats, category_name),
                    selected_id(brands, brand_name), selected_id(grades, grade_name) if grade_name != "Select" else None,
                    selected_colours, int(quantity), user)
                st.session_state.add_preview = {"variants": variants, "item_name": item_name.strip(),
                    "model_name": model_name.strip(), "category_id": selected_id(cats, category_name),
                    "brand_id": selected_id(brands, brand_name),
                    "grade_id": selected_id(grades, grade_name) if grade_name != "Select" else None,
                    "quantity": int(quantity), "mrp": mrp or None, "purchase_price": purchase or None,
                    "selling_price": selling or None, "lot_number": lot_number.strip() or None,
                    "source_url": source_url.strip() or None}
            except Exception as e:
                st.error(str(e))
    draft = st.session_state.get("add_preview")
    if draft:
        st.subheader("Review variants")
        st.dataframe(pd.DataFrame(draft["variants"]), use_container_width=True, hide_index=True)
        if st.button("Confirm and save inventory", type="primary"):
            try:
                result = save_inventory_variants(draft, user)
                for msg in result:
                    st.success(msg)
                st.session_state.pop("add_preview", None)
                rerun()
            except Exception as e:
                st.error(str(e))

elif page == "Manage":
    require_admin()
    st.title("Manage master data")
    kind_labels = {"categories": "Categories", "brands": "Brands", "grades": "Grades", "colours": "Colours", "abbreviations": "Abbreviations"}
    kind = st.selectbox("Manage", list(kind_labels.keys()), format_func=lambda x: kind_labels[x])
    rows = list_master(kind, active_only=False)
    with st.expander("Add new record", expanded=True):
        with st.form("add_master"):
            if kind == "abbreviations":
                original = st.text_input("Original word or phrase")
                replacement = st.text_input("Abbreviation")
                priority = st.number_input("Priority (higher wins ties)", min_value=0, value=0, step=1)
                name, code = original.strip(), replacement.strip().upper()
            else:
                name = st.text_input("Name")
                code = st.text_input("Code (optional; used in SKU)", placeholder="Auto-generated if blank").strip().upper()
                priority = 0
            add = st.form_submit_button("Add")
        if add:
            try:
                if not name.strip():
                    raise ValueError("Name is required.")
                add_master(kind, name.strip(), code or None, int(priority), user)
                st.success("Record added.")
                rerun()
            except Exception as e:
                st.error(str(e))
    st.subheader("Bulk import from CSV")
    st.caption("Upload a CSV with two columns: name and code. Column names are case-insensitive (for example, Name, CODE, or NAME, Code).")
    uploaded_csv = st.file_uploader("Choose master-data CSV", type=["csv"], key=f"master_csv_{kind}")
    if uploaded_csv is not None:
        try:
            import io
            import_csv_df = pd.read_csv(uploaded_csv, dtype=str, keep_default_na=False)
            normalized_columns = {str(col).strip().casefold(): col for col in import_csv_df.columns}
            if "name" not in normalized_columns or "code" not in normalized_columns:
                st.error("CSV must contain both required columns: name and code (case-insensitive).")
            else:
                import_csv_df = import_csv_df.rename(columns={
                    normalized_columns["name"]: "name",
                    normalized_columns["code"]: "code"
                })
                import_csv_df = import_csv_df[["name", "code"]]
                st.write("Preview")
                st.dataframe(import_csv_df.head(20), use_container_width=True, hide_index=True)
                st.caption(f"{len(import_csv_df)} row(s) found.")
                if st.button("Import CSV rows", type="primary", key=f"import_master_csv_{kind}"):
                    results = bulk_add_master(kind, import_csv_df.to_dict(orient="records"), user)
                    result_df = pd.DataFrame(results)
                    imported_count = int((result_df["status"] == "Imported").sum()) if not result_df.empty else 0
                    skipped_count = int((result_df["status"] == "Skipped").sum()) if not result_df.empty else 0
                    st.success(f"Import finished: {imported_count} imported, {skipped_count} skipped.")
                    st.session_state[f"master_import_results_{kind}"] = result_df.to_dict(orient="records")
                    rerun()
        except Exception as e:
            st.error(f"Could not read CSV: {e}")

    previous_import_results = st.session_state.get(f"master_import_results_{kind}")
    if previous_import_results:
        st.subheader("Last CSV import results")
        st.dataframe(pd.DataFrame(previous_import_results), use_container_width=True, hide_index=True)

    st.subheader("Existing records")
    if rows:
        display = pd.DataFrame(rows)
        st.dataframe(display, use_container_width=True, hide_index=True)
        options = {f"{r['name']} — {r.get('code') or ''} (ID {r['id']})": r for r in rows}
        selected = st.selectbox("Select record to update/deactivate", ["Select"] + list(options.keys()))
        if selected != "Select":
            row = options[selected]
            with st.form("update_master"):
                new_name = st.text_input("Name / original phrase", row["name"])
                new_code = st.text_input("Code / abbreviation", row.get("code") or "")
                active = st.checkbox("Active", value=bool(row["active"]))
                update = st.form_submit_button("Save master data")
            if update:
                try:
                    update_master(kind, row["id"], new_name.strip(), new_code.strip().upper() or None, active, user)
                    st.success("Master data updated. Existing SKUs are not changed automatically.")
                    rerun()
                except Exception as e:
                    st.error(str(e))
    else:
        st.info("No records configured yet.")

elif page == "User Management":
    require_admin()
    st.title("User Management")
    with st.form("create_user"):
        username = st.text_input("Username")
        password = st.text_input("Temporary password", type="password")
        role = st.selectbox("Role", ["standard", "admin"])
        submitted = st.form_submit_button("Create user", type="primary")
    if submitted:
        try:
            create_user(username.strip(), password, role, user)
            st.success("User created.")
            rerun()
        except Exception as e:
            st.error(str(e))
    st.subheader("Existing users")
    users = list_users()
    if users:
        st.dataframe(pd.DataFrame(users), use_container_width=True, hide_index=True)
        options = {f"{u['username']} ({u['role']}) — ID {u['id']}": u for u in users}
        selected = st.selectbox("Select user to activate/deactivate", ["Select"] + list(options.keys()))
        if selected != "Select":
            target = options[selected]
            desired = st.checkbox("Account active", value=bool(target["active"]), key=f"active_{target['id']}")
            if st.button("Save account status"):
                try:
                    set_user_active(target["id"], desired, user)
                    st.success("Account status updated.")
                    rerun()
                except Exception as e:
                    st.error(str(e))

elif page == "Audit History":
    require_admin()
    st.title("Audit History")
    rows = audit_rows()
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("No audit events recorded yet.")
