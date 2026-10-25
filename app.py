import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import json
import re
import io as _io
from db import (
    init_db, authenticate, create_user, list_users, set_user_active,
    list_master, add_master, bulk_add_master, bulk_add_sub_categories, update_master,
    list_inventory, preview_variants, save_inventory_variants,
    update_inventory, get_inventory_by_id, audit_rows,
    change_password, admin_count, deactivate_master, inventory_suggestions,
    list_sub_categories,
)
from sku import generate_sku, colour_code
from urllib.parse import urlparse
from pathlib import Path
from amazon_integration import fetch_amazon_product

image_sorter_component = components.declare_component(
    "inventory_image_sorter", path=str(Path(__file__).parent / "image_sorter")
)
autocomplete_component = components.declare_component(
    "inventory_autocomplete", path=str(Path(__file__).parent / "autocomplete")
)

st.set_page_config(page_title="Inventory & SKU Manager", page_icon="📦", layout="wide")
init_db()

# ── Global CSS ─────────────────────────────────────────────────────────────
st.markdown("""<style>
:root {
  --c-primary: #2563eb; --c-primary-dark: #1d4ed8;
  --c-border: #e2e8f0; --c-surface: #f8fafc;
  --c-text: #0f172a; --c-muted: #64748b; --radius: 12px;
}
[data-testid="stAppViewContainer"] > .main { background: #f1f5f9; }
.main .block-container { padding-top: 2.5rem !important; padding-bottom: 3rem !important; max-width: 1200px; }

/* Sidebar */
[data-testid="stSidebar"] { background: #0f172a !important; border-right: 1px solid #1e293b; }
[data-testid="stSidebar"] p,[data-testid="stSidebar"] span,
[data-testid="stSidebar"] label,[data-testid="stSidebar"] div { color: #94a3b8 !important; }
[data-testid="stSidebar"] strong,[data-testid="stSidebar"] b { color: #f1f5f9 !important; }

/* Metrics */
[data-testid="metric-container"] {
  background:#fff !important; border:1px solid var(--c-border) !important;
  border-radius:var(--radius) !important; padding:1rem 1.25rem !important;
  box-shadow:0 1px 3px rgba(0,0,0,.05) !important;
  border-left:4px solid var(--c-primary) !important; border-top:none !important;
}
[data-testid="metric-container"] [data-testid="stMetricValue"] { font-size:1.75rem !important; font-weight:700 !important; color:var(--c-text) !important; }
[data-testid="metric-container"] [data-testid="stMetricLabel"] { font-size:0.68rem !important; font-weight:700 !important; text-transform:uppercase !important; letter-spacing:0.07em !important; color:var(--c-muted) !important; }

/* Buttons */
.stButton > button[kind="primary"], .stFormSubmitButton > button[kind="primary"] {
  background:var(--c-primary) !important; border:none !important;
  border-radius:8px !important; font-weight:600 !important; height:2.5rem !important;
}
.stButton > button[kind="primary"]:hover,.stFormSubmitButton > button[kind="primary"]:hover { background:var(--c-primary-dark) !important; }
.stButton > button[kind="secondary"] { border:1.5px solid var(--c-border) !important; border-radius:8px !important; background:#fff !important; }
.stDownloadButton > button { border-radius:8px !important; font-size:0.82rem !important; font-weight:600 !important; }

/* Forms / Expanders / Alerts */
[data-testid="stForm"] { background:#fff; border:1px solid var(--c-border); border-radius:var(--radius); padding:1.5rem 1.75rem; box-shadow:0 1px 3px rgba(0,0,0,.04); }
[data-testid="stExpander"] { border:1px solid var(--c-border) !important; border-radius:var(--radius) !important; background:#fff; }
[data-testid="stAlert"] { border-radius:8px !important; }
[data-testid="stDataFrame"] > div { border-radius:8px; overflow:hidden; }

/* Input labels */
.stTextInput label p,.stSelectbox label p,.stNumberInput label p,
.stMultiSelect label p,.stTextArea label p,.stFileUploader label p {
  font-size:0.72rem !important; font-weight:700 !important;
  letter-spacing:0.05em !important; text-transform:uppercase !important; color:#475569 !important;
}

/* Section dividers */
.erp-section { display:flex; align-items:center; gap:8px; padding:0.4rem 0 0.5rem; border-bottom:2px solid var(--c-border); margin:1.5rem 0 0.9rem; }
.erp-section-dot { width:7px; height:7px; border-radius:50%; background:var(--c-primary); flex-shrink:0; }
.erp-section-label { font-size:0.68rem; font-weight:700; letter-spacing:0.1em; text-transform:uppercase; color:#475569; }

/* Page header */
.erp-page-title { font-size:1.6rem; font-weight:700; color:var(--c-text); letter-spacing:-0.02em; margin:0 0 4px; }
.erp-page-subtitle { font-size:0.85rem; color:var(--c-muted); margin:0 0 1.5rem; }

/* Filter count */
.filter-count { font-size:0.75rem; color:var(--c-muted); margin:6px 0 -4px; font-weight:500; }
.amazon-header { background:#fff7ed; border:1px solid #fed7aa; border-radius:10px; padding:0.75rem 1.25rem; margin:0.75rem 0; display:flex; align-items:center; gap:10px; font-size:0.85rem; color:#9a3412; font-weight:600; }

/* Filter count */
.filter-count { font-size:0.75rem; color:var(--c-muted); margin:6px 0 -4px; font-weight:500; }
</style>""", unsafe_allow_html=True)


# ── Helpers ────────────────────────────────────────────────────────────────
def rerun(): st.rerun()
def logged_in(): return bool(st.session_state.get("user"))
def is_admin(): return logged_in() and st.session_state.user["role"] == "admin"

def require_admin():
    if not is_admin():
        st.error("You do not have permission to access this section.")
        st.stop()

def master_options(kind): return list_master(kind, active_only=True)

def option_labels(rows): return ["Select"] + [r["name"] for r in rows]

def selected_id(rows, selected_name):
    for r in rows:
        if r["name"] == selected_name:
            return r["id"]
    return None

def clean_amazon_url(value):
    value = str(value or "").strip()
    match = re.search(r"https?://[^\s)\]]+", value)
    return match.group(0).replace("\\&","&").replace("\\_","_") if match else value

def sec(label, icon=""):
    st.markdown(
        f'<div class="erp-section"><span class="erp-section-dot"></span>'
        f'<span class="erp-section-label">{icon} {label}</span></div>',
        unsafe_allow_html=True)

def page_header(title, subtitle=""):
    st.markdown(
        f'<div class="erp-page-title">{title}</div>'
        + (f'<div class="erp-page-subtitle">{subtitle}</div>' if subtitle else ""),
        unsafe_allow_html=True)

def validate_prices(mrp, selling, purchase, prefix=""):
    """Raise ValueError if price order is violated. Skips zeros."""
    if mrp > 0 and selling > 0 and mrp < selling:
        raise ValueError(f"{prefix}MRP (₹{mrp:.0f}) must be ≥ Selling Price (₹{selling:.0f}).")
    if selling > 0 and purchase > 0 and selling < purchase:
        raise ValueError(f"{prefix}Selling Price (₹{selling:.0f}) must be ≥ Purchase Price (₹{purchase:.0f}).")

def normalize_amazon_data(data):
    specs = data.get("specifications", {}) or {}
    if isinstance(specs, str):
        try: specs = json.loads(specs)
        except Exception: specs = {"Specifications": specs}
    images = data.get("images", []) or []
    cleaned = []
    for u in images:
        url = clean_amazon_url(u)
        if url and url not in cleaned: cleaned.append(url)
    about = data.get("amazon_about_this_item", "") or ""
    if isinstance(about, list): about = json.dumps(about, ensure_ascii=False, indent=2)
    return {
        "amazon_asin":            str(data.get("asin", "")         or ""),
        "amazon_title":           str(data.get("title", "")        or ""),
        "amazon_price":           str(data.get("price", "")        or ""),
        "amazon_rating":          str(data.get("rating", "")       or "").strip(),
        "amazon_reviews":         str(data.get("reviews", "")      or ""),
        "amazon_availability":    str(data.get("availability", "") or ""),
        "description":            str(data.get("description", "")  or ""),
        "amazon_about_this_item": str(about),
        "specifications": specs if isinstance(specs, dict) else {},
        "images": cleaned,
    }

def _safe_json(val, fallback="[]"):
    if not isinstance(val, str): return json.loads(fallback)
    try: return json.loads(val or fallback)
    except Exception: return json.loads(fallback)

def _safe_str(val, fallback=""):
    return val if isinstance(val, str) else fallback

def build_shopify_rows(inv_rows):
    rows = []
    for item in inv_rows:
        images  = _safe_json(item.get("images_json"), "[]")
        colours = _safe_json(item.get("colours_json"), "[]")
        name_s  = _safe_str(item.get("item_name", ""))
        model_s = _safe_str(item.get("model_name", ""))
        handle  = re.sub(r"[^a-z0-9]+", "-", f"{name_s} {model_s}".lower()).strip("-")
        handle  = f"{handle}-{re.sub(r'[^a-z0-9]', '', _safe_str(item.get('sku','')).lower())}"
        title   = _safe_str(item.get("amazon_title")) or name_s
        desc    = _safe_str(item.get("description"))
        rows.append({
            "Handle": handle, "Title": title, "Body (HTML)": desc,
            "Vendor": _safe_str(item.get("brand")),
            "Type": _safe_str(item.get("sub_category") or item.get("category")),
            "Tags": "", "Published": "TRUE",
            "Option1 Name": "Color" if colours else "",
            "Option1 Value": " / ".join(colours) if colours else "",
            "Variant SKU": _safe_str(item.get("sku")),
            "Variant Inventory Qty": item.get("quantity", 0),
            "Variant Price": _safe_str(item.get("selling_price")),
            "Variant Compare At Price": _safe_str(item.get("mrp")),
            "Variant Requires Shipping": "TRUE", "Variant Taxable": "TRUE",
            "Image Src": images[0] if images else "",
            "Image Position": 1 if images else "",
            "Image Alt Text": title, "SEO Title": title, "SEO Description": desc[:320],
        })
        for pos, img in enumerate(images[1:], start=2):
            rows.append({"Handle": handle, "Image Src": img,
                         "Image Position": pos, "Image Alt Text": f"{title} {pos}"})
    return pd.DataFrame(rows)


# ── Auth gate ───────────────────────────────────────────────────────────────
if not logged_in():
    _, col, _ = st.columns([1, 1.4, 1])
    with col:
        st.markdown('<br>', unsafe_allow_html=True)
        st.markdown(
            '<div style="text-align:center;font-size:2.5rem;margin-bottom:0.5rem">📦</div>'
            '<div style="text-align:center;font-size:1.5rem;font-weight:700;color:#0f172a;margin-bottom:0.25rem">Inventory Manager</div>'
            '<div style="text-align:center;font-size:0.85rem;color:#64748b;margin-bottom:1.5rem">Sign in to manage your products and SKUs</div>',
            unsafe_allow_html=True)
        with st.form("login"):
            username  = st.text_input("Username")
            password  = st.text_input("Password", type="password")
            submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)
        if submitted:
            user = authenticate(username.strip(), password)
            if user:
                st.session_state.user = user
                rerun()
            else:
                st.error("Invalid username or password.")
        st.caption("First run? Create an admin: `python setup_admin.py`")
    st.stop()


# ── Sidebar ─────────────────────────────────────────────────────────────────
user = st.session_state.user
with st.sidebar:
    st.markdown(
        '<div style="padding:0.5rem 0 1.25rem">'
        '<div style="font-size:1.1rem;font-weight:700;color:#f8fafc;letter-spacing:-0.01em">📦 Inventory Manager</div>'
        '</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div style="background:#1e293b;border-radius:10px;padding:0.75rem 1rem;margin-bottom:1rem">'
        f'<div style="font-size:0.65rem;font-weight:700;letter-spacing:0.08em;text-transform:uppercase;color:#475569;margin-bottom:2px">Signed in as</div>'
        f'<div style="font-size:0.95rem;font-weight:600;color:#f1f5f9">{user["username"]}</div>'
        f'<div style="font-size:0.72rem;color:#64748b;margin-top:2px">'
        f'{"🔑 Admin" if user["role"] == "admin" else "👤 Standard"}'
        f'</div></div>', unsafe_allow_html=True)

    pages = ["Dashboard", "Inventory", "Add Inventory"]
    if is_admin():
        pages += ["Manage", "User Management", "Audit History"]

    if "nav_page" not in st.session_state:
        st.session_state.nav_page = "Dashboard" if is_admin() else "Add Inventory"
    if st.session_state.nav_page not in pages:
        st.session_state.nav_page = pages[0]

    page = st.radio("Navigation", pages,
                    index=pages.index(st.session_state.nav_page),
                    key="nav_radio", label_visibility="collapsed")
    st.session_state.nav_page = page
    st.markdown('<div style="height:1rem"></div>', unsafe_allow_html=True)
    if st.button("Sign out", use_container_width=True):
        st.session_state.clear(); rerun()


# ── Dashboard ────────────────────────────────────────────────────────────────
if page == "Dashboard":
    page_header("Dashboard", "Overview of your inventory.")
    inv = list_inventory()
    df  = pd.DataFrame(inv)
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Inventory Records", len(df))
    c2.metric("Total Units",   int(df["quantity"].sum()) if not df.empty else 0)
    c3.metric("Unique SKUs",   df["sku"].nunique()       if not df.empty else 0)
    c4.metric("Zero-stock",    int((df["quantity"]==0).sum()) if not df.empty else 0)

    sec("RECENT ACTIVITY")
    if df.empty:
        st.info("No inventory yet. Use **Add Inventory** to create your first item.")
    else:
        recent = df.sort_values("updated_at", ascending=False).head(10)
        show   = [c for c in ["sku","item_name","brand","sub_category","category","quantity",
                               "selling_price","created_by","modified_by","updated_at"]
                  if c in recent.columns]
        st.dataframe(recent[show], use_container_width=True, hide_index=True, column_config={
            "sku":           st.column_config.TextColumn("SKU"),
            "item_name":     st.column_config.TextColumn("Item Name", width="large"),
            "brand":         st.column_config.TextColumn("Brand"),
            "sub_category":  st.column_config.TextColumn("Sub-Category"),
            "category":      st.column_config.TextColumn("Category"),
            "quantity":      st.column_config.NumberColumn("Qty", format="%d"),
            "selling_price": st.column_config.NumberColumn("Sell Price (₹)", format="%.0f"),
            "created_by":    st.column_config.TextColumn("Created By"),
            "modified_by":   st.column_config.TextColumn("Modified By"),
            "updated_at":    st.column_config.DatetimeColumn("Last Updated", format="DD MMM YYYY, HH:mm"),
        })


# ── Inventory ────────────────────────────────────────────────────────────────
elif page == "Inventory":
    page_header("Inventory", "Search, filter and manage all stock records.")
    inv = list_inventory()
    df  = pd.DataFrame(inv)

    if df.empty:
        st.info("No inventory records found. Use **Add Inventory** to create your first item.")
    else:
        k1,k2,k3,k4 = st.columns(4)
        k1.metric("Total Records", len(df))
        k2.metric("Total Units",  int(df["quantity"].sum()))
        k3.metric("In Stock",     int((df["quantity"]>0).sum()))
        k4.metric("Out of Stock", int((df["quantity"]==0).sum()))

        sec("SEARCH & FILTER")
        search = st.text_input("Search", placeholder="Search by SKU, item name or model number...", label_visibility="collapsed")
        f1,f2,f3,f4,f5 = st.columns(5)
        category  = f1.selectbox("Category",     ["All"] + sorted(df["category"].dropna().unique().tolist()))
        sub_vals  = sorted(df["sub_category"].dropna().unique().tolist()) if "sub_category" in df.columns else []
        sub_cat_f = f2.selectbox("Sub-Category", ["All"] + sub_vals)
        brand     = f3.selectbox("Brand",        ["All"] + sorted(df["brand"].dropna().unique().tolist()))
        grade     = f4.selectbox("Grade",        ["All"] + sorted(df["grade"].dropna().unique().tolist()))
        colour    = f5.selectbox("Colour",       ["All"] + sorted(df["colour_code"].dropna().unique().tolist()))

        filtered = df.copy()
        if search:
            mask = filtered[["sku","item_name","model_name"]].fillna("").astype(str).apply(
                lambda col: col.str.contains(search, case=False, regex=False)).any(axis=1)
            filtered = filtered[mask]
        for _col, _val in [("category",category),("sub_category",sub_cat_f),
                            ("brand",brand),("grade",grade),("colour_code",colour)]:
            if _val != "All" and _col in filtered.columns:
                filtered = filtered[filtered[_col].fillna("") == _val]

        display_df = filtered.copy()
        display_df["Status"] = display_df["quantity"].apply(
            lambda q: "Out of Stock" if q==0 else ("Low Stock" if q<=3 else "In Stock"))

        SHOW_COLS = ["sku","item_name","model_name","brand","sub_category","category","grade",
                     "colour_code","Status","quantity","mrp","selling_price","lot_number",
                     "created_by","modified_by","updated_at"]
        display_df = display_df[[c for c in SHOW_COLS if c in display_df.columns]]

        st.markdown(f'<div class="filter-count">{len(filtered)} record{"s" if len(filtered)!=1 else ""} found</div>',
                    unsafe_allow_html=True)
        st.dataframe(display_df, use_container_width=True, hide_index=True, column_config={
            "sku":           st.column_config.TextColumn("SKU", width="medium"),
            "item_name":     st.column_config.TextColumn("Item Name", width="large"),
            "model_name":    st.column_config.TextColumn("Model"),
            "brand":         st.column_config.TextColumn("Brand"),
            "sub_category":  st.column_config.TextColumn("Sub-Category"),
            "category":      st.column_config.TextColumn("Category"),
            "grade":         st.column_config.TextColumn("Grade"),
            "colour_code":   st.column_config.TextColumn("Colour"),
            "Status":        st.column_config.TextColumn("Status"),
            "quantity":      st.column_config.NumberColumn("Qty", format="%d", width="small"),
            "mrp":           st.column_config.NumberColumn("MRP (₹)", format="%.0f"),
            "selling_price": st.column_config.NumberColumn("Sell Price (₹)", format="%.0f"),
            "lot_number":    st.column_config.TextColumn("LOT"),
            "created_by":    st.column_config.TextColumn("Created By"),
            "modified_by":   st.column_config.TextColumn("Modified By"),
            "updated_at":    st.column_config.DatetimeColumn("Last Updated", format="DD MMM YYYY"),
        })

        if not filtered.empty:
            sec("EXPORT")
            col_dl1, col_dl2, col_dl3, _ = st.columns([1,1,1.3,2.7])
            with col_dl1:
                st.download_button("⬇ CSV", filtered.to_csv(index=False).encode("utf-8"),
                                   "inventory_export.csv", "text/csv")
            with col_dl2:
                _buf = _io.BytesIO()
                filtered.to_excel(_buf, index=False, engine="openpyxl")
                st.download_button("⬇ Excel", _buf.getvalue(), "inventory_export.xlsx",
                                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            with col_dl3:
                shopify_df = build_shopify_rows(filtered.to_dict(orient="records"))
                st.download_button("⬇ Shopify CSV", shopify_df.to_csv(index=False).encode("utf-8"),
                                   "shopify_import.csv", "text/csv")

        if is_admin():
            sec("EDIT RECORD")
            choices = {f"[{r['sku']}]  {r['item_name']}  (ID {r['id']})": r["id"] for r in inv}
            choice  = st.selectbox("Select a record to edit", ["— select —"] + list(choices.keys()))
            if choice != "— select —":
                row = get_inventory_by_id(choices[choice])
                with st.form("edit_inventory"):
                    sec("PRODUCT INFO")
                    ei1, ei2 = st.columns(2)
                    item_name  = ei1.text_input("Item name *", row["item_name"])
                    model_name = ei2.text_input("Model name",  row["model_name"] or "")

                    sec("STOCK & PRICING")
                    ep1,ep2,ep3,ep4 = st.columns(4)
                    quantity = ep1.number_input("Quantity", min_value=0, step=1, value=int(row["quantity"]))
                    mrp      = ep2.number_input("MRP (₹)",  min_value=0.0, value=float(row["mrp"] or 0), step=1.0)
                    purchase = ep3.number_input("Purchase Price (₹)", min_value=0.0, value=float(row["purchase_price"] or 0), step=1.0)
                    selling  = ep4.number_input("Selling Price (₹)",  min_value=0.0, value=float(row["selling_price"] or 0), step=1.0)

                    sec("STORAGE & PHYSICAL")
                    es1,es2,es3,es4,es5 = st.columns(5)
                    lot_number    = es1.text_input("LOT Number",  row.get("lot_number") or "")
                    osp           = es2.number_input("OSP (Online Selling Price)", min_value=0.0, step=1.0, value=float(row.get("osp") or 0))
                    capacity      = es3.number_input("Capacity",  min_value=0, step=1, value=int(row.get("capacity") or 0))
                    unit_opts     = ["Select","L","mL","kg","g","GB","TB","W","kW","Ah","mAh","pieces","other"]
                    cur_unit      = row.get("capacity_unit") or "Select"
                    capacity_unit = es4.selectbox("Unit", unit_opts, index=unit_opts.index(cur_unit) if cur_unit in unit_opts else 0)
                    source_url    = es5.text_input("Source URL", row["source_url"] or "")

                    sec("AMAZON / PRODUCT METADATA")
                    with st.expander("Expand to edit Amazon details", expanded=False):
                        am1,am2 = st.columns(2)
                        edit_asin         = am1.text_input("ASIN",         row.get("amazon_asin") or "")
                        edit_amazon_title = am2.text_input("Amazon title",  row.get("amazon_title") or "")
                        am3,am4,am5 = st.columns(3)
                        edit_amazon_price = am3.text_input("Amazon price",  row.get("amazon_price") or "")
                        edit_rating       = am4.text_input("Rating",        row.get("amazon_rating") or "")
                        edit_reviews      = am5.text_input("Reviews",       row.get("amazon_reviews") or "")
                        edit_availability = st.text_input("Availability",   row.get("amazon_availability") or "")
                        edit_description  = st.text_area("Description",     row.get("description") or "", height=120)
                        edit_about        = st.text_area("About this item", row.get("amazon_about_this_item") or "", height=120)
                        try:
                            _specs_default = json.dumps(json.loads(row.get("specifications_json") or "{}"), indent=2)
                        except Exception:
                            _specs_default = "{}"
                        edit_specs_text = st.text_area("Specifications (JSON)", _specs_default, height=140)
                    save_btn = st.form_submit_button("Save changes", type="primary")

                if save_btn:
                    try:
                        if not item_name.strip():
                            raise ValueError("Item name is required.")
                        if source_url and urlparse(source_url).scheme not in ("http","https"):
                            raise ValueError("Source URL must start with http:// or https://")
                        validate_prices(mrp, selling, purchase)
                        specs = json.loads(edit_specs_text or "{}")
                        if not isinstance(specs, dict):
                            raise ValueError("Specifications must be a JSON object.")
                        update_inventory(row["id"], {
                            "item_name": item_name.strip(), "model_name": model_name.strip(),
                            "quantity": int(quantity), "mrp": mrp or None,
                            "purchase_price": purchase or None, "selling_price": selling or None,
                            "lot_number": lot_number.strip() or None, "source_url": source_url.strip() or None,
                            "osp": osp or None, "capacity": int(capacity) if capacity else None,
                            "capacity_unit": capacity_unit if capacity_unit != "Select" else None,
                            "amazon_asin": edit_asin.strip(), "amazon_title": edit_amazon_title.strip(),
                            "amazon_price": edit_amazon_price.strip(), "amazon_rating": edit_rating.strip(),
                            "amazon_reviews": edit_reviews.strip(), "amazon_availability": edit_availability.strip(),
                            "description": edit_description, "amazon_about_this_item": edit_about,
                            "specifications": specs,
                        }, user)
                        st.success("Record updated successfully.")
                        rerun()
                    except Exception as e:
                        st.error(str(e))


# ── Add Inventory ─────────────────────────────────────────────────────────────
elif page == "Add Inventory":
    page_header("Add Inventory", "Fill in product details, then fetch Amazon data and confirm to save.")

    cats    = master_options("categories")
    brands  = master_options("brands")
    grades  = master_options("grades")
    colours = master_options("colours")
    if not cats or not brands:
        st.warning("An administrator must add at least one active category and brand in **Manage** before inventory can be added.")

    # Form counter — incremented on save to reset all widgets
    if "add_form_counter" not in st.session_state:
        st.session_state.add_form_counter = 0
    fc = st.session_state.add_form_counter

    # ── PRODUCT IDENTITY ──────────────────────────────────────────────────────
    sec("PRODUCT IDENTITY")
    ai1, ai2 = st.columns(2)

    # Helper: resolve current text + suggestions for an autocomplete field
    def _ac_field(col, key_name, label, placeholder):
        cur_text = st.session_state.get(f"{key_name}_{fc}", "")
        suggs    = inventory_suggestions(key_name, cur_text) if len(cur_text) >= 4 else []
        result   = col.empty()
        with result:
            val = autocomplete_component(
                label=label, placeholder=placeholder,
                value=cur_text, suggestions=suggs[:8],
                key=f"{key_name}_ac_{fc}",
                default={"text": "", "selected": False},
            )
        if isinstance(val, dict):
            new_text = val.get("text", "")
            if new_text != cur_text:
                st.session_state[f"{key_name}_{fc}"] = new_text
                rerun()
        return st.session_state.get(f"{key_name}_{fc}", "")

    item_name  = _ac_field(ai1, "item_name",  "Item Name *", "e.g. Washing Machine, Air Cooler")
    model_name = _ac_field(ai2, "model_name", "Model Name",  "e.g. Magic Clean 8kg, MAGCLE80")

    # ── CLASSIFICATION ────────────────────────────────────────────────────────
    sec("CLASSIFICATION")
    ac1,ac2,ac3,ac4,ac5 = st.columns(5)
    category_name    = ac1.selectbox("Category *",   option_labels(cats), key=f"cat_{fc}")
    selected_cat_id  = selected_id(cats, category_name)
    sub_cats         = list_sub_categories(category_id=selected_cat_id, active_only=True) if selected_cat_id else []
    if not sub_cats:
        sub_cat_name = ac2.selectbox(
            "Sub-category",
            ["— select category first —" if not selected_cat_id else "— none configured —"],
            disabled=True, key=f"subcat_{fc}")
    else:
        sub_cat_name = ac2.selectbox("Sub-category", ["Select"] + [r["name"] for r in sub_cats],
                                     key=f"subcat_{fc}")
    brand_name       = ac3.selectbox("Brand *",      option_labels(brands), key=f"brand_{fc}")
    grade_name       = ac4.selectbox("Grade",        option_labels(grades), key=f"grade_{fc}")
    selected_colours = ac5.multiselect("Colours",    [r["name"] for r in colours], key=f"colours_{fc}")

    # ── STOCK & PRICING ───────────────────────────────────────────────────────
    sec("STOCK & PRICING")
    ap1,ap2,ap3,ap4 = st.columns(4)
    quantity = ap1.number_input("Quantity *",          min_value=1, step=1, value=1, key=f"qty_{fc}")
    mrp      = ap2.number_input("MRP (₹)",             min_value=0.0, value=0.0, step=1.0, key=f"mrp_{fc}")
    purchase = ap3.number_input("Purchase Price (₹)",  min_value=0.0, value=0.0, step=1.0, key=f"pur_{fc}")
    selling  = ap4.number_input("Selling Price (₹)",   min_value=0.0, value=0.0, step=1.0, key=f"sell_{fc}")

    # ── STORAGE & PHYSICAL ────────────────────────────────────────────────────
    sec("STORAGE & PHYSICAL")
    as1,as2,as3,as4 = st.columns(4)
    lot_number    = as1.text_input("LOT Number", key=f"lot_{fc}")
    osp           = as2.number_input("OSP (Online Selling Price)", min_value=0.0, value=0.0, step=1.0, key=f"osp_{fc}")
    capacity      = as3.number_input("Capacity", min_value=0, step=1, value=0, key=f"cap_{fc}")
    capacity_unit = as4.selectbox("Unit", ["Select","L","mL","kg","g","GB","TB","W","kW","Ah","mAh","pieces","other"], key=f"unit_{fc}")

    # ── SOURCE / AMAZON ───────────────────────────────────────────────────────
    sec("SOURCE / AMAZON")
    st.markdown(
        '<div class="amazon-header">🛒  Paste an Amazon product URL — the tool will fetch title, description, specs and images automatically.</div>',
        unsafe_allow_html=True)
    source_url = st.text_input("Amazon / Source URL", placeholder="https://www.amazon.in/dp/...", key=f"src_{fc}")

    st.markdown('<div style="height:0.5rem"></div>', unsafe_allow_html=True)
    if st.button("🔍  Fetch Amazon details & preview SKU", type="primary"):
        if not item_name.strip() or category_name == "Select" or brand_name == "Select":
            st.error("Item name, category and brand are required before fetching.")
        elif source_url and urlparse(clean_amazon_url(source_url)).scheme not in ("http","https"):
            st.error("Source URL must start with http:// or https://")
        else:
            try:
                validate_prices(mrp, selling, purchase)
                sub_cat_id = selected_id(sub_cats, sub_cat_name) if sub_cats and sub_cat_name not in ("Select","— none configured —","— select category first —") else None
                variants   = preview_variants(
                    item_name.strip(), model_name.strip(),
                    selected_cat_id, sub_cat_id,
                    selected_id(brands, brand_name),
                    selected_id(grades, grade_name) if grade_name != "Select" else None,
                    selected_colours, int(quantity), user)
                variant = variants[0]

                # Default amazon data
                amazon_data = {"amazon_asin":"","amazon_title":"","amazon_price":"","amazon_rating":"",
                               "amazon_reviews":"","amazon_availability":"","description":"",
                               "amazon_about_this_item":"","specifications":{},"images":[]}
                # Prices to use in draft (may come from existing record)
                draft_mrp      = mrp or None
                draft_purchase = purchase or None
                draft_selling  = selling or None

                if not variant.get("current_quantity", 0):
                    # New SKU — fetch from Amazon if URL provided
                    if source_url:
                        try:
                            with st.spinner("Fetching product details from Amazon — this may take up to 30 seconds..."):
                                raw_amazon = fetch_amazon_product(clean_amazon_url(source_url))
                            amazon_data = normalize_amazon_data(raw_amazon)
                            st.success("Amazon details fetched. Review and edit below before saving.")
                        except Exception as scrape_error:
                            st.warning(f"Amazon fetch incomplete: {scrape_error}. You can enter details manually.")
                else:
                    # Existing SKU — pre-fill review fields from the existing DB record
                    existing_id = variant.get("existing_id")
                    if existing_id:
                        existing_row = get_inventory_by_id(existing_id)
                        if existing_row:
                            _specs = {}
                            try: _specs = json.loads(existing_row.get("specifications_json") or "{}")
                            except Exception: pass
                            _images = []
                            try: _images = json.loads(existing_row.get("images_json") or "[]")
                            except Exception: pass
                            amazon_data = {
                                "amazon_asin":            existing_row.get("amazon_asin") or "",
                                "amazon_title":           existing_row.get("amazon_title") or "",
                                "amazon_price":           existing_row.get("amazon_price") or "",
                                "amazon_rating":          existing_row.get("amazon_rating") or "",
                                "amazon_reviews":         existing_row.get("amazon_reviews") or "",
                                "amazon_availability":    existing_row.get("amazon_availability") or "",
                                "description":            existing_row.get("description") or "",
                                "amazon_about_this_item": existing_row.get("amazon_about_this_item") or "",
                                "specifications":         _specs,
                                "images":                 _images,
                            }
                            # Use existing prices when user hasn't entered new ones
                            draft_mrp      = mrp      or existing_row.get("mrp")      or None
                            draft_purchase = purchase or existing_row.get("purchase_price") or None
                            draft_selling  = selling  or existing_row.get("selling_price")  or None
                    st.info(f"SKU already exists with **{variant['current_quantity']} units** in stock. Fields are pre-filled with existing data. Confirm to add {int(quantity)} more unit(s).")

                st.session_state.add_preview = {
                    "variants": variants,
                    "item_name": item_name.strip(), "model_name": model_name.strip(),
                    "category_id": selected_cat_id, "sub_category_id": sub_cat_id,
                    "brand_id": selected_id(brands, brand_name),
                    "grade_id": selected_id(grades, grade_name) if grade_name != "Select" else None,
                    "quantity": int(quantity), "mrp": draft_mrp,
                    "purchase_price": draft_purchase, "selling_price": draft_selling,
                    "lot_number": lot_number.strip() or None,
                    "source_url": clean_amazon_url(source_url) if source_url else None,
                    "osp": osp or None, "capacity": int(capacity) if capacity else None,
                    "capacity_unit": capacity_unit if capacity_unit != "Select" else None,
                    **amazon_data,
                }
            except Exception as e:
                st.error(str(e))

    # ── Review & save ─────────────────────────────────────────────────────────
    draft = st.session_state.get("add_preview")
    if draft:
        st.markdown('<hr style="margin:2rem 0 1rem;border-color:#e2e8f0">', unsafe_allow_html=True)
        sku_val      = draft["variants"][0]["sku"]
        action       = draft["variants"][0]["action"]
        conflict     = draft["variants"][0].get("conflict", False)
        current_qty  = draft["variants"][0].get("current_quantity", 0)
        qty_to_add   = draft["variants"][0].get("quantity_to_add", 0)

        if conflict:
            banner_color = "#dc2626"
            banner_action = "⛔ SKU attribute conflict — cannot save"
            banner_note   = "This SKU already belongs to a different product. Resolve before saving."
        elif current_qty > 0:
            banner_color  = "#2563eb"
            banner_action = "📦 SKU exists — adding to current stock"
            banner_note   = f"Current stock: {current_qty} unit(s). After confirm: {current_qty + qty_to_add} unit(s)."
        else:
            banner_color  = "#16a34a"
            banner_action = "✅ No existing SKU — proceed to create a new one"
            banner_note   = "This is a brand-new product record."

        st.markdown(
            f'<div style="background:{banner_color};color:#fff;border-radius:10px;padding:1rem 1.5rem;'
            f'margin-bottom:0.5rem">'
            f'<div style="display:flex;align-items:center;justify-content:space-between">'
            f'<div><div style="font-size:0.65rem;font-weight:600;letter-spacing:0.1em;'
            f'text-transform:uppercase;opacity:0.8">Generated SKU</div>'
            f'<div style="font-size:1.15rem;font-weight:700;font-family:monospace;'
            f'letter-spacing:0.08em;margin-top:2px">{sku_val}</div></div>'
            f'<div style="text-align:right"><div style="font-size:0.85rem;font-weight:700">{banner_action}</div>'
            f'<div style="font-size:0.75rem;opacity:0.85;margin-top:2px">{banner_note}</div></div>'
            f'</div></div>',
            unsafe_allow_html=True)

        sec("REVIEW — PRODUCT DETAILS")
        rv1,rv2 = st.columns(2)
        review_item_name  = rv1.text_input("Item name *",  value=draft["item_name"],  key=f"rv_item_{sku_val}")
        review_model_name = rv2.text_input("Model name",   value=draft["model_name"], key=f"rv_model_{sku_val}")
        rp1,rp2,rp3,rp4 = st.columns(4)
        review_osp          = rp1.number_input("OSP (Online Selling Price)", min_value=0.0, step=1.0, value=float(draft.get("osp") or 0), key=f"rv_osp_{sku_val}")
        review_capacity     = rp2.number_input("Capacity",   min_value=0, step=1, value=int(draft.get("capacity") or 0), key=f"rv_cap_{sku_val}")
        unit_opts           = ["Select","L","mL","kg","g","GB","TB","W","kW","Ah","mAh","pieces","other"]
        existing_unit       = draft.get("capacity_unit") or "Select"
        review_cap_unit     = rp3.selectbox("Unit", unit_opts, index=unit_opts.index(existing_unit) if existing_unit in unit_opts else 0, key=f"rv_unit_{sku_val}")
        review_lot          = rp4.text_input("LOT Number", value=draft.get("lot_number") or "", key=f"rv_lot_{sku_val}")
        rm1,rm2,rm3 = st.columns(3)
        review_mrp      = rm1.number_input("MRP (₹)",             min_value=0.0, value=float(draft.get("mrp") or 0), step=1.0, key=f"rv_mrp_{sku_val}")
        review_purchase = rm2.number_input("Purchase Price (₹)",   min_value=0.0, value=float(draft.get("purchase_price") or 0), step=1.0, key=f"rv_pur_{sku_val}")
        review_selling  = rm3.number_input("Selling Price (₹)",    min_value=0.0, value=float(draft.get("selling_price") or 0), step=1.0, key=f"rv_sel_{sku_val}")
        review_source_url = st.text_input("Source URL", value=draft.get("source_url") or "", key=f"rv_src_{sku_val}")

        sec("REVIEW — AMAZON DETAILS")
        ra1,ra2 = st.columns(2)
        review_asin  = ra1.text_input("ASIN",         value=draft.get("amazon_asin",""),  key=f"rv_asin_{sku_val}")
        review_title = ra2.text_input("Amazon title", value=draft.get("amazon_title",""), key=f"rv_atitle_{sku_val}")
        ra3,ra4,ra5 = st.columns(3)
        review_amazon_price = ra3.text_input("Amazon price",  value=draft.get("amazon_price",""), key=f"rv_aprice_{sku_val}")
        review_rating       = ra4.text_input("Rating",        value=draft.get("amazon_rating",""), key=f"rv_rating_{sku_val}")
        review_reviews      = ra5.text_input("Reviews count", value=draft.get("amazon_reviews",""), key=f"rv_reviews_{sku_val}")
        review_avail        = st.text_input("Availability", value=draft.get("amazon_availability",""), key=f"rv_avail_{sku_val}")
        review_desc         = st.text_area("Description", value=draft.get("description",""), height=140, key=f"rv_desc_{sku_val}")
        review_about        = st.text_area("About this item", value=draft.get("amazon_about_this_item",""), height=120, key=f"rv_about_{sku_val}")
        specs_text          = st.text_area("Specifications (JSON)",
                                           value=json.dumps(draft.get("specifications",{}), ensure_ascii=False, indent=2),
                                           height=160, key=f"rv_specs_{sku_val}")

        sec("REVIEW — PRODUCT IMAGES")
        st.caption("Drag to reorder · First image is the cover · Hover a tile to remove · Use + to add a URL")
        edited_images = image_sorter_component(images=draft.get("images",[]), component_key=sku_val,
                                               default=draft.get("images",[]), key=f"rv_imgs_{sku_val}")
        if not isinstance(edited_images, list):
            edited_images = draft.get("images", [])

        st.markdown('<div style="height:0.75rem"></div>', unsafe_allow_html=True)
        col_save, col_cancel, _ = st.columns([1.5,1,4])
        if col_save.button("✅  Confirm & save", type="primary", disabled=conflict):
            try:
                if not review_item_name.strip():
                    raise ValueError("Item name is required.")
                validate_prices(review_mrp, review_selling, review_purchase)
                if review_source_url.strip() and urlparse(clean_amazon_url(review_source_url)).scheme not in ("http","https"):
                    raise ValueError("Source URL must start with http:// or https://")
                specs = json.loads(specs_text or "{}")
                if not isinstance(specs, dict):
                    raise ValueError("Specifications must be a JSON object.")
                ordered_images = []
                for img_url in edited_images:
                    url = clean_amazon_url(img_url)
                    if url and urlparse(url).scheme in ("http","https") and url not in ordered_images:
                        ordered_images.append(url)
                draft.update({
                    "item_name": review_item_name.strip(), "model_name": review_model_name.strip(),
                    "osp": review_osp or None, "capacity": int(review_capacity) if review_capacity else None,
                    "capacity_unit": review_cap_unit if review_cap_unit != "Select" else None,
                    "mrp": review_mrp or None, "purchase_price": review_purchase or None,
                    "selling_price": review_selling or None, "lot_number": review_lot.strip() or None,
                    "source_url": clean_amazon_url(review_source_url) if review_source_url.strip() else None,
                    "amazon_asin": review_asin.strip(), "amazon_title": review_title.strip(),
                    "amazon_price": review_amazon_price.strip(), "amazon_rating": review_rating.strip(),
                    "amazon_reviews": review_reviews.strip(), "amazon_availability": review_avail.strip(),
                    "description": review_desc, "amazon_about_this_item": review_about,
                    "specifications": specs, "images": ordered_images,
                })
                result = save_inventory_variants(draft, user)
                for msg in result: st.success(msg)
                # Clear draft and reset form (increment counter resets all field keys)
                st.session_state.pop("add_preview", None)
                st.session_state.add_form_counter = fc + 1
                rerun()
            except Exception as e:
                st.error(f"Could not save: {e}")
        if col_cancel.button("✕  Cancel"):
            st.session_state.pop("add_preview", None); rerun()



# ── Manage ────────────────────────────────────────────────────────────────────
elif page == "Manage":
    require_admin()
    page_header("Manage Master Data",
                "Configure categories, sub-categories, brands, grades, colours and SKU abbreviations.")

    kind_labels = {
        "categories":     "Categories",
        "sub_categories": "Sub-Categories",
        "brands":         "Brands",
        "grades":         "Grades",
        "colours":        "Colours",
        "abbreviations":  "Abbreviations",
    }
    kind = st.selectbox("Data type", list(kind_labels.keys()), format_func=lambda x: kind_labels[x])
    rows = list_sub_categories(category_id=None, active_only=False) if kind == "sub_categories" else list_master(kind, active_only=False)

    sec("ADD NEW RECORD")
    with st.form("add_master"):
        if kind == "abbreviations":
            original    = st.text_input("Original word or phrase *")
            replacement = st.text_input("Abbreviation (code) *")
            priority    = st.number_input("Priority (higher wins ties)", min_value=0, value=0, step=1)
            name, code, parent_id_add = original.strip(), replacement.strip().upper(), None
        elif kind == "sub_categories":
            cats_all      = list_master("categories", active_only=True)
            parent_choice = st.selectbox("Parent category *", option_labels(cats_all), key="add_sc_parent")
            parent_id_add = selected_id(cats_all, parent_choice) if parent_choice != "Select" else None
            mg1, mg2 = st.columns(2)
            name     = mg1.text_input("Sub-category name *")
            code     = mg2.text_input("Code *", placeholder="e.g. FTLWM").strip().upper()
            priority = 0
        else:
            mg1, mg2 = st.columns(2)
            name     = mg1.text_input("Name *")
            code     = mg2.text_input("Code *", placeholder="e.g. WM, ACO — required").strip().upper()
            priority, parent_id_add = 0, None
        add_btn = st.form_submit_button("Add record", type="primary")
    if add_btn:
        try:
            if not name.strip(): raise ValueError("Name is required.")
            if not code:         raise ValueError("Code is required.")
            add_master(kind, name.strip(), code, int(priority), user, parent_id=parent_id_add)
            st.success("Record added.")
            rerun()
        except Exception as e:
            st.error(str(e))

    sec("BULK IMPORT")
    if kind == "sub_categories":
        st.caption("Upload a CSV with columns: `parent`, `name`, `code` — sub-categories are only added when the parent category exists.")
        _sc_required = ["parent", "name", "code"]
    else:
        st.caption("Upload a CSV with columns: `name` and `code` (both required).")
        _sc_required = ["name", "code"]
    uploaded_csv = st.file_uploader("Choose CSV file", type=["csv"], key=f"master_csv_{kind}")
    if uploaded_csv is not None:
        try:
            import io
            import_df = pd.read_csv(uploaded_csv, dtype=str, keep_default_na=False)
            norm_cols = {str(c).strip().casefold(): c for c in import_df.columns}
            missing = [r for r in _sc_required if r not in norm_cols]
            if missing:
                st.error(f"CSV is missing required column(s): {', '.join('`' + m + '`' for m in missing)}")
            else:
                rename_map = {norm_cols[r]: r for r in _sc_required}
                import_df  = import_df.rename(columns=rename_map)[_sc_required]
                st.dataframe(import_df.head(20), use_container_width=True, hide_index=True)
                st.caption(f"{len(import_df)} row(s) found.")
                if st.button("Import", type="primary", key=f"import_csv_{kind}"):
                    if kind == "sub_categories":
                        results = bulk_add_sub_categories(import_df.to_dict(orient="records"), user)
                    else:
                        results = bulk_add_master(kind, import_df.to_dict(orient="records"), user)
                    result_df = pd.DataFrame(results)
                    imported  = int((result_df["status"]=="Imported").sum()) if not result_df.empty else 0
                    skipped   = int((result_df["status"]=="Skipped").sum()) if not result_df.empty else 0
                    st.success(f"Import finished: {imported} imported, {skipped} skipped.")
                    st.session_state[f"import_results_{kind}"] = result_df.to_dict(orient="records")
                    rerun()
        except Exception as e:
            st.error(f"Could not read CSV: {e}")
    prev = st.session_state.get(f"import_results_{kind}")
    if prev:
        st.dataframe(pd.DataFrame(prev), use_container_width=True, hide_index=True)

    sec("EXISTING RECORDS")
    if rows:
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        if kind == "sub_categories":
            opts = {f"{r['name']} ({r['category_name']}) — {r['code']} (ID {r['id']})": r for r in rows}
        else:
            opts = {f"{r['name']} — {r.get('code') or ''} (ID {r['id']})": r for r in rows}
        selected = st.selectbox("Select record to update / deactivate", ["— select —"] + list(opts.keys()))
        if selected != "— select —":
            row = opts[selected]
            with st.form("update_master"):
                if kind == "sub_categories":
                    cats_all      = list_master("categories", active_only=True)
                    cur_parent    = next((c for c in cats_all if c["id"] == row["parent_id"]), None)
                    parent_opts   = option_labels(cats_all)
                    parent_idx    = parent_opts.index(cur_parent["name"]) if cur_parent and cur_parent["name"] in parent_opts else 0
                    new_par_name  = st.selectbox("Parent category *", parent_opts, index=parent_idx)
                    new_parent_id = selected_id(cats_all, new_par_name) if new_par_name != "Select" else row["parent_id"]
                    mu1, mu2 = st.columns(2)
                    new_name = mu1.text_input("Sub-category name *", row["name"])
                    new_code = mu2.text_input("Code *", row.get("code") or "")
                else:
                    new_parent_id = None
                    mu1, mu2 = st.columns(2)
                    new_name = mu1.text_input("Name *", row["name"])
                    new_code = mu2.text_input("Code *", row.get("code") or "")
                active  = st.checkbox("Active", value=bool(row["active"]))
                upd_btn = st.form_submit_button("Save changes", type="primary")
            if upd_btn:
                try:
                    update_master(kind, row["id"], new_name.strip(), new_code.strip().upper(), active, user, parent_id=new_parent_id)
                    st.success("Record updated.")
                    rerun()
                except Exception as e:
                    st.error(str(e))
    else:
        st.info("No records configured yet.")


# ── User Management ───────────────────────────────────────────────────────────
elif page == "User Management":
    require_admin()
    page_header("User Management", "Create accounts and manage access.")
    sec("CREATE USER")
    with st.form("create_user"):
        cu1,cu2,cu3 = st.columns(3)
        username  = cu1.text_input("Username")
        password  = cu2.text_input("Temporary password", type="password")
        role      = cu3.selectbox("Role", ["standard","admin"])
        submitted = st.form_submit_button("Create user", type="primary")
    if submitted:
        try:
            create_user(username.strip(), password, role, user)
            st.success("User created.")
            rerun()
        except Exception as e:
            st.error(str(e))

    sec("EXISTING USERS")
    users = list_users()
    if users:
        st.dataframe(pd.DataFrame(users), use_container_width=True, hide_index=True)
        opts     = {f"{u['username']} ({u['role']}) — ID {u['id']}": u for u in users}
        selected = st.selectbox("Select user to activate/deactivate", ["— select —"] + list(opts.keys()))
        if selected != "— select —":
            target  = opts[selected]
            desired = st.checkbox("Account active", value=bool(target["active"]), key=f"active_{target['id']}")
            if st.button("Save account status", type="primary"):
                try:
                    set_user_active(target["id"], desired, user)
                    st.success("Account status updated.")
                    rerun()
                except Exception as e:
                    st.error(str(e))


# ── Audit History ─────────────────────────────────────────────────────────────
elif page == "Audit History":
    require_admin()
    page_header("Audit History", "Full log of every create, update and status change.")
    _AUDIT_PAGE_SIZE = 100
    if "audit_page" not in st.session_state:
        st.session_state.audit_page = 0
    rows, total = audit_rows(limit=_AUDIT_PAGE_SIZE, offset=st.session_state.audit_page * _AUDIT_PAGE_SIZE)
    start = st.session_state.audit_page * _AUDIT_PAGE_SIZE + 1
    end   = min((st.session_state.audit_page + 1) * _AUDIT_PAGE_SIZE, total)
    st.caption(f"Showing {start}–{end} of {total} events")
    if rows:
        df_audit = pd.DataFrame(rows)
        st.dataframe(df_audit, use_container_width=True, hide_index=True)
        st.download_button("⬇ Download this page as CSV",
                           df_audit.to_csv(index=False).encode("utf-8"),
                           f"audit_history_page{st.session_state.audit_page+1}.csv", "text/csv")
    else:
        st.info("No audit events recorded yet.")
    nav1,_,nav3 = st.columns([1,4,1])
    with nav1:
        if st.session_state.audit_page > 0:
            if st.button("← Previous"):
                st.session_state.audit_page -= 1; rerun()
    with nav3:
        if end < total:
            if st.button("Next →"):
                st.session_state.audit_page += 1; rerun()
