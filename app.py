import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import json
import re
import io as _io
import uuid
import hashlib
from db import (
    init_db, authenticate, create_user, list_users, set_user_active,
    list_master, add_master, bulk_add_master, bulk_add_sub_categories, update_master,
    list_inventory, preview_variants, save_inventory_variants,
    update_inventory, get_inventory_by_id, get_product_by_handle, audit_rows,
    change_password, admin_count, deactivate_master, inventory_suggestions,
    list_sub_categories, update_shopify_status, update_product_content, mark_synced,
    list_taxonomy_leaves, taxonomy_count, replace_taxonomy, search_taxonomy,
)
from sku import generate_sku, colour_code, extract_physical_from_specs
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

# ── Auto-seed admin from Streamlit secrets (Cloud deployment) ─────────────
# Set [admin] username and password in the Streamlit Cloud secrets manager.
# On a fresh DB the admin is created automatically; no setup_admin.py needed.
try:
    _sec = st.secrets.get("admin", {})
    _su, _sp = _sec.get("username", ""), _sec.get("password", "")
    if _su and _sp and admin_count() == 0:
        from db import create_initial_admin
        create_initial_admin(_su, _sp)
except Exception:
    pass  # secrets not configured or admin already exists — ignore silently

# ── ScraperAPI key (needed on Streamlit Cloud to bypass Amazon IP blocks) ─────
# Add SCRAPERAPI_KEY = "your_key" to Streamlit Cloud secrets.
# Free tier: https://www.scraperapi.com (1,000 requests/month)
_SCRAPERAPI_KEY = ""
try:
    _SCRAPERAPI_KEY = (
        st.secrets.get("SCRAPERAPI_KEY", "")
        or st.secrets.get("scraper", {}).get("api_key", "")
    )
except Exception:
    pass

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


def _save_uploaded_image(uploaded) -> str:
    """Persist an uploaded image to static/uploads (content-hash filename) and
    return its local URL. The bytes are staged to Shopify only at push time."""
    _fhash = hashlib.md5(uploaded.getvalue()).hexdigest()
    _ext   = Path(uploaded.name).suffix.lower() or ".jpg"
    _fname = f"{_fhash}{_ext}"
    _udir  = Path(__file__).parent / "static" / "uploads"
    _udir.mkdir(parents=True, exist_ok=True)
    (_udir / _fname).write_bytes(uploaded.getvalue())
    return f"http://localhost:8501/app/static/uploads/{_fname}"


def _sync_taxonomy_from_dataset():
    """Download Shopify's published Standard Product Taxonomy and store its leaf
    categories. Returns the number of leaves loaded."""
    import requests, itertools
    url = "https://raw.githubusercontent.com/Shopify/product-taxonomy/main/dist/en/categories.json"
    data = requests.get(url, timeout=180).json()
    allcats = itertools.chain.from_iterable(v.get("categories", []) for v in data.get("verticals", []))
    leaves = [{"gid": c["id"], "name": c["name"], "full_name": c["full_name"]}
              for c in allcats if not c.get("children")]
    replace_taxonomy(leaves)
    _taxonomy_options.clear()
    return len(leaves)


@st.cache_data(ttl=3600, show_spinner=False)
def _taxonomy_options():
    """Cached dropdown options for the Shopify category field — ONE entry per
    unique last-node name. Returns (names_sorted, {name: (gid, full_name)}).
    Duplicate leaf names collapse to the shortest (most general) full path."""
    best = {}
    for l in list_taxonomy_leaves():
        n = l["name"]
        if n not in best or len(l["full_name"]) < len(best[n][1]):
            best[n] = (l["gid"], l["full_name"])
    names = sorted(best.keys(), key=str.casefold)
    return names, best


@st.cache_data(ttl=3600, show_spinner=False)
def _lookup_shopify_category(leaf: str):
    """Resolve a Shopify taxonomy leaf to its full path for live validation.
    Returns the full path string, '' if no match, or None if Shopify is unreachable.
    Cached per leaf for an hour so typing doesn't hammer the API."""
    leaf = (leaf or "").strip()
    if not leaf:
        return ""
    try:
        from shopify_integration import ShopifyClient
        cfg   = st.secrets.get("shopify", {})
        store = cfg.get("store", "") or ""
        token = cfg.get("access_token", "") or ""
        cid   = cfg.get("client_id", "") or ""
        csec  = cfg.get("client_secret", "") or ""
        if not store or (not token and not (cid and csec)):
            return None
        client = ShopifyClient(store, access_token=token, client_id=cid, client_secret=csec)
        q = """query($s: String!) { taxonomy { categories(first: 10, search: $s) {
                 nodes { name fullName isLeaf } } } }"""
        nodes = client.graphql(q, {"s": leaf})["taxonomy"]["categories"]["nodes"]
        key = leaf.casefold()
        exact = [n for n in nodes if n.get("isLeaf") and str(n.get("name", "")).casefold() == key]
        if exact:
            return exact[0]["fullName"]
        leafs = [n for n in nodes if n.get("isLeaf")]
        return (leafs[0]["fullName"] if leafs else (nodes[0]["fullName"] if nodes else ""))
    except Exception:
        return None


@st.dialog("Add image for a variant")
def _upload_image_dialog():
    _ctx = st.session_state.get("_upload_dialog_ctx", {})
    _var_labels = _ctx.get("var_labels", [])
    _pool_key   = _ctx.get("pool_key")
    _vimgs_key  = _ctx.get("vimgs_key")
    _ckey_key   = _ctx.get("ckey_key")
    _first_sku  = _ctx.get("first_sku")
    _draft      = _ctx.get("draft", {})

    if not _var_labels:
        st.info("No variants found.")
        return

    _vi_sel = st.selectbox("Assign to variant", _var_labels, key="_dlg_variant_sel")
    _uf = st.file_uploader(
        "Choose image from computer",
        type=["jpg", "jpeg", "png", "webp"],
        key="_dlg_file_uploader",
    )
    if _uf is not None:
        st.image(_uf, width=180)
        if st.button("✅ Add to product images", type="primary", key="_dlg_add_btn"):
            _fhash = hashlib.md5(_uf.getvalue()).hexdigest()
            _ext   = Path(_uf.name).suffix.lower() or ".jpg"
            _fname = f"{_fhash}{_ext}"
            _udir  = Path(__file__).parent / "static" / "uploads"
            _udir.mkdir(parents=True, exist_ok=True)
            (_udir / _fname).write_bytes(_uf.getvalue())
            _img_url   = f"http://localhost:8501/app/static/uploads/{_fname}"
            _cur_pool  = st.session_state.get(_pool_key, list(_draft.get("images", [])))
            if _img_url not in _cur_pool:
                _cur_pool = list(_cur_pool) + [_img_url]
            st.session_state[_pool_key]  = _cur_pool
            _vi_idx = _var_labels.index(_vi_sel) if _vi_sel in _var_labels else 0
            _vimgs  = st.session_state.get(_vimgs_key, {})
            _vimgs[_vi_idx] = _img_url
            st.session_state[_vimgs_key] = _vimgs
            st.session_state[_ckey_key]  = f"{_first_sku}_u{len(_cur_pool)}"
            st.rerun()


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

    # Parse weight and packed dimensions from specs (shared, robust parser)
    _phys = extract_physical_from_specs(specs if isinstance(specs, dict) else {})
    weight_grams  = _phys["weight_grams"]
    weight_unit   = _phys["weight_unit"]
    packed_length = _phys["packed_length"]
    packed_width  = _phys["packed_width"]
    packed_height = _phys["packed_height"]

    # Parse what's in the box
    whats_in_the_box = ""
    for bkey in ("Included Components", "What's in the box", "In the Box", "Box Contents", "Whats in the box"):
        val = specs.get(bkey) or ""
        if val:
            whats_in_the_box = str(val)
            break

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
        "whats_in_the_box": whats_in_the_box,
        "weight_grams":  weight_grams,
        "weight_unit":   weight_unit,
        "packed_length": packed_length,
        "packed_width":  packed_width,
        "packed_height": packed_height,
    }

def _safe_json(val, fallback="[]"):
    if not isinstance(val, str): return json.loads(fallback)
    try: return json.loads(val or fallback)
    except Exception: return json.loads(fallback)

def _safe_str(val, fallback=""):
    return val if isinstance(val, str) else fallback

def build_shopify_rows(inv_rows):
    """Build a Shopify product import CSV grouped by product (Handle).

    Shopify requires all variants of the same product to share the same Handle.
    Product-level columns (Title, Body, Vendor, Tags …) appear only on the
    first row of each product group; subsequent rows carry only Handle + variant
    columns.  Images are distributed one-per-row; extra images beyond the number
    of variants get image-only rows at the end of the group.
    """
    from collections import OrderedDict

    def _make_handle(title):
        h = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        return h or "product"

    # Step 1 — group by product_handle (stable product id = SKU without colour/grade).
    # Fall back to item_name for any legacy rows that have no handle yet.
    by_handle: OrderedDict = OrderedDict()
    for item in inv_rows:
        key = _safe_str(item.get("product_handle", "")).strip() \
            or _make_handle(_safe_str(item.get("item_name", "")))
        if key not in by_handle:
            by_handle[key] = []
        by_handle[key].append(item)

    # Step 2 — handle is the product_handle itself (already unique per product)
    products: OrderedDict = OrderedDict()
    for key, variants in by_handle.items():
        name = _safe_str(variants[0].get("item_name", ""))
        # Shopify handles must be lowercase slugs
        h = key.lower()
        products[h] = {"name": name, "handle": h, "variants": variants}

    rows = []

    for handle, product in products.items():
        variants = product["variants"]
        first    = variants[0]
        name_s   = product["name"]

        desc      = _safe_str(first.get("description"))
        seo_title = _safe_str(first.get("amazon_title")) or name_s

        # Tags: store + brand (Title case) + category + sub-category + all grades
        tag_parts = ["Bettr Stores"]
        _brand_tag = " ".join(w.capitalize() for w in _safe_str(first.get("brand")).split())
        if _brand_tag:
            tag_parts.append(_brand_tag)
        for _tf in ("category", "sub_category"):
            val = _safe_str(first.get(_tf))
            if val:
                tag_parts.append(val)
        for _v in variants:
            g = _safe_str(_v.get("grade"))
            if g and g not in tag_parts:
                tag_parts.append(g)
        tags = ", ".join(tag_parts)

        specs_raw = first.get("specifications_json") or "{}"
        try:
            specs_dict = json.loads(specs_raw) if isinstance(specs_raw, str) else (specs_raw or {})
            specs_str  = "\n".join(f"{k}: {v}" for k, v in specs_dict.items()) if specs_dict else ""
        except Exception:
            specs_str = ""

        # Collect all product images (union across all variants, deduplicated)
        all_images: list = []
        seen_imgs:  set  = set()
        for _v in variants:
            for img in _safe_json(_v.get("images_json"), "[]"):
                if img and img not in seen_imgs:
                    all_images.append(img)
                    seen_imgs.add(img)

        img_cursor = 0

        for v_idx, item in enumerate(variants):
            colours      = _safe_json(item.get("colours_json"), "[]")
            opt2_name    = "Color" if colours else ""
            opt2_value   = "; ".join(c.lower() for c in colours) if colours else ""
            color_meta   = "; ".join(c.lower() for c in colours) if colours else ""
            is_first_row = (v_idx == 0)

            cur_img     = all_images[img_cursor] if img_cursor < len(all_images) else ""
            cur_img_pos = img_cursor + 1          if img_cursor < len(all_images) else ""

            rows.append({
                "Handle":        handle,
                # Product-level columns — only on the first row of this product
                "Title":         name_s   if is_first_row else "",
                "Body (HTML)":   desc     if is_first_row else "",
                "Vendor":        _safe_str(first.get("brand"))        if is_first_row else "",
                "Type":          _safe_str(first.get("sub_category")) if is_first_row else "",
                "Tags":          tags     if is_first_row else "",
                "Published":     "TRUE"   if is_first_row else "",
                "Gift Card":     "FALSE"  if is_first_row else "",
                "Status":        "active" if is_first_row else "",
                "SEO Title":     seo_title  if is_first_row else "",
                "SEO Description": desc[:320] if is_first_row else "",
                "Key Features (product.metafields.custom.key_features)":
                    _safe_str(first.get("amazon_about_this_item")) if is_first_row else "",
                "Model  (product.metafields.custom.model)":
                    re.sub(r"\s{2,}", " ", re.sub(r"\b(?:19|20)\d{2}\b", "", _safe_str(first.get("model_name")))).strip(" -,/") if is_first_row else "",
                "Specifications (product.metafields.custom.specifications)":
                    specs_str if is_first_row else "",
                "What's Inside the Box (product.metafields.custom.whats_in_the_box)":
                    _safe_str(first.get("whats_in_the_box")) if is_first_row else "",
                # Option columns — on every variant row
                "Option1 Name":  "Grade",
                "Option1 Value": _safe_str(item.get("grade")) or "Default Title",
                "Option2 Name":  opt2_name,
                "Option2 Value": opt2_value,
                "Option2 Linked To": "",
                # Variant columns
                "Variant SKU":                  _safe_str(item.get("sku")),
                "Variant Inventory Qty":        item.get("quantity", 0),
                "Variant Price":                _safe_str(item.get("selling_price")) or "0",
                "Variant Compare At Price":     _safe_str(item.get("osp")),
                "Variant Inventory Tracker":    "shopify",
                "Variant Inventory Policy":     "deny",
                "Variant Fulfillment Service":  "manual",
                "Variant Requires Shipping":    "TRUE",
                "Variant Taxable":              "FALSE",
                "Variant HS Code":              _safe_str(item.get("hs_code")),
                "Variant Packed Dimension Unit": "cm",
                "Variant Grams":        item.get("weight_grams") or "",
                "Variant Weight Unit":  _safe_str(item.get("weight_unit")),
                "Variant Packed Length": item.get("packed_length") or "",
                "Variant Packed Width":  item.get("packed_width") or "",
                "Variant Packed Height": item.get("packed_height") or "",
                # Per-variant metafields
                "OSP (product.metafields.custom.osp)":              _safe_str(item.get("osp")),
                "Color (product.metafields.shopify.color-pattern)": color_meta,
                "Warehouse Location (product.metafields.custom.warehouse_location)": _safe_str(item.get("warehouse_location")),
                "Cost per item": _safe_str(item.get("purchase_price")),
                # Variant Image — pins a specific product-level image to this variant
                "Variant Image": item.get("variant_image") or "",
                # One image per row
                "Image Src":      cur_img,
                "Image Position": cur_img_pos,
                "Image Alt Text": name_s if cur_img else "",
            })

            if img_cursor < len(all_images):
                img_cursor += 1

        # Extra image-only rows for remaining images after all variants are covered
        while img_cursor < len(all_images):
            rows.append({
                "Handle":         handle,
                "Image Src":      all_images[img_cursor],
                "Image Position": img_cursor + 1,
                "Image Alt Text": f"{name_s} {img_cursor + 1}",
            })
            img_cursor += 1

    return pd.DataFrame(rows)


def _shopify_push_ui(push_rows):
    """Run the Shopify push and render results.
    Returns {"synced_ids": [...], "failed_ids": [...]} so callers can update DB.
    """
    sku_to_id  = {str(r.get("sku","")).strip(): r["id"] for r in push_rows if r.get("sku") and r.get("id")}
    # Snapshot the quantity at push time so synced_qty advances to exactly what we
    # pushed, not whatever the row holds later (guards a concurrent top-up).
    sku_to_qty = {str(r.get("sku","")).strip(): int(r.get("quantity") or 0) for r in push_rows if r.get("sku")}
    synced_qty_by_id, synced_status_only_ids, failed_ids = {}, [], []
    try:
        from shopify_integration import ShopifyClient, ShopifyAPIError
        _sh_cfg       = st.secrets.get("shopify", {})
        _sh_store     = _sh_cfg.get("store", "") or ""
        _sh_token     = _sh_cfg.get("access_token", "") or ""
        _sh_client_id = _sh_cfg.get("client_id", "") or ""
        _sh_secret    = _sh_cfg.get("client_secret", "") or ""
        if not _sh_store or (not _sh_token and not (_sh_client_id and _sh_secret)):
            st.error("Shopify credentials not configured. Add `[shopify]` store + client_id + client_secret to Streamlit secrets.")
            return {"synced_qty_by_id": {}, "synced_status_only_ids": [], "failed_ids": list(sku_to_id.values())}
        client = ShopifyClient(_sh_store, access_token=_sh_token, client_id=_sh_client_id, client_secret=_sh_secret)

        # Locally-uploaded images live at http://localhost:.../static/uploads/… which
        # Shopify's servers can't fetch. At push time, upload their bytes to Shopify's
        # staging bucket and swap the URL for the ingestable resourceUrl. A stable alt
        # (content-hash filename) lets re-pushes dedup instead of duplicating.
        _LOCAL_MARKER   = "/static/uploads/"
        _uploads_dir    = Path(__file__).parent / "static" / "uploads"
        _stage_cache    = {}   # local_url -> resourceUrl (upload each file once per push)
        alt_overrides   = {}   # resourceUrl -> stable alt
        def _resolve_img(url):
            if not isinstance(url, str) or _LOCAL_MARKER not in url:
                return url
            if url in _stage_cache:
                return _stage_cache[url]
            fname = url.split(_LOCAL_MARKER, 1)[1].split("?")[0]
            fpath = _uploads_dir / fname
            if not fpath.exists():
                st.caption(f"  ⚠ local image not found, skipped: {fname}")
                return url
            try:
                _low  = fname.lower()
                _mime = ("image/png" if _low.endswith(".png")
                         else "image/webp" if _low.endswith(".webp") else "image/jpeg")
                resource_url = client.stage_upload(fpath.read_bytes(), fname, _mime)
            except Exception as _e:
                st.caption(f"  ⚠ image upload failed for {fname}: {_e}")
                return url
            _stage_cache[url]            = resource_url
            alt_overrides[resource_url]  = "img_local_" + fname.rsplit(".", 1)[0]
            return resource_url

        def _rewrite_row_images(row):
            try:
                _imgs = json.loads(row.get("images_json") or "[]")
            except Exception:
                _imgs = []
            if isinstance(_imgs, list) and _imgs:
                row["images_json"] = json.dumps([_resolve_img(u) for u in _imgs])
            if row.get("variant_image"):
                row["variant_image"] = _resolve_img(row["variant_image"])

        def _group_key(r):
            h = str(r.get("product_handle", "")).strip().casefold()
            if h:
                return (h,)
            return (
                str(r.get("item_name","")).casefold().strip(),
                str(r.get("model_name","")).casefold().strip(),
                str(r.get("brand","")).casefold().strip(),
            )

        groups = {}
        for row in push_rows:
            # Delta to add to Shopify = units received since the last successful push.
            row["inventory_delta"] = int(row.get("quantity") or 0) - int(row.get("shopify_synced_qty") or 0)
            _rewrite_row_images(row)
            k = _group_key(row)
            groups.setdefault(k, []).append(row)
        group_list = list(groups.values())

        st.info(f"Pushing {len(push_rows)} SKU(s) as {len(group_list)} product(s) to Shopify…")
        prog = st.progress(0)
        results, errors = [], []
        for idx, group in enumerate(group_list):
            try:
                res = client.upsert_product_group(group, alt_overrides=alt_overrides)
                results.append(res)
            except Exception as exc:
                skus = ", ".join(str(r.get("sku","?")) for r in group)
                errors.append({"skus": skus, "error": str(exc), "group": group})
            prog.progress((idx + 1) / len(group_list))

        prog.empty()
        if results:
            total_created = sum(len(r["created"]) for r in results)
            total_updated = sum(len(r["updated"]) for r in results)
            st.success(f"Done — {len(results)} product(s) pushed · {total_created} SKU(s) created · {total_updated} SKU(s) updated.")
            for r in results:
                parts = []
                if r["created"]: parts.append(f"🆕 created: {', '.join(r['created'])}")
                if r["updated"]: parts.append(f"✏️ updated: {', '.join(r['updated'])}")
                img = r["images"]
                parts.append(f"images +{img['added']}/skip {img['skipped']}")
                st.markdown(f"[View product]({r['product_url']}) — {' · '.join(parts)}")
                if r.get("inventory_notes"):
                    for sku, note in r["inventory_notes"].items():
                        st.caption(f"  ⚠ inventory {sku}: {note}")
                inv_ok    = set(s.strip() for s in r.get("inventory_ok", []))
                inv_notes = r.get("inventory_notes", {}) or {}
                for sku in r["created"] + r["updated"]:
                    skey = sku.strip()
                    rid = sku_to_id.get(skey)
                    if not rid:
                        continue
                    _note = str(inv_notes.get(skey, ""))
                    if skey in inv_ok:
                        # Inventory actually applied → mark synced AND advance
                        # shopify_synced_qty to what we pushed (delta baseline).
                        synced_qty_by_id[rid] = sku_to_qty.get(skey, 0)
                    elif _note.startswith("skipped"):
                        # Product/variant IS on Shopify but inventory was skipped
                        # (write_inventory scope off). Mark synced for visibility, but
                        # DON'T advance synced_qty — so once the scope is granted the
                        # pending quantity still syncs as a delta.
                        synced_status_only_ids.append(rid)
                    else:
                        # A real inventory error — mark failed; delta preserved.
                        failed_ids.append(rid)
        if errors:
            st.error(f"{len(errors)} product(s) failed:")
            for e in errors:
                st.markdown(f"- **{e['skus']}**: {e['error']}")
                for row in e.get("group", []):
                    rid = sku_to_id.get(str(row.get("sku","")).strip())
                    if rid:
                        failed_ids.append(rid)
    except ImportError:
        st.error("shopify_integration.py not found.")
        failed_ids = list(sku_to_id.values())
    return {"synced_qty_by_id": synced_qty_by_id,
            "synced_status_only_ids": synced_status_only_ids,
            "failed_ids": failed_ids}


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
        st.caption("Admin credentials are set via Streamlit secrets or the `setup_admin.py` script.")
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

    pages = ["Dashboard", "Inventory", "Add Inventory", "Manage Shopify"]
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

    # Handle a push triggered from the Products tab BEFORE loading inventory,
    # so the table reflects the updated Shopify status.
    if st.session_state.get("inv_push_rows"):
        _push_rows = st.session_state.pop("inv_push_rows")
        _status = _shopify_push_ui(_push_rows)
        if _status["synced_qty_by_id"]:
            mark_synced(_status["synced_qty_by_id"])
        if _status.get("synced_status_only_ids"):
            update_shopify_status(_status["synced_status_only_ids"], "synced")
        if _status["failed_ids"]:
            update_shopify_status(_status["failed_ids"], "failed")

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

        # Stable product id — fall back to item_name for any legacy row without one
        if "product_handle" not in df.columns:
            df["product_handle"] = ""
        df["product_id"] = df["product_handle"].fillna("").replace("", pd.NA)
        df["product_id"] = df["product_id"].fillna(df["item_name"])
        # Product-level shopify status (derived from each variant's status):
        #   ✅ Sync      — every variant synced
        #   ◐ Modified   — some synced, some new/modified/failed (needs re-push)
        #   Not Sync     — nothing synced yet
        def _product_status(series):
            statuses = [str(x).strip().lower() if pd.notna(x) and str(x).strip() else "" for x in series]
            n = len(statuses)
            n_synced = sum(1 for s in statuses if s == "synced")
            if n and n_synced == n:
                return "✅ Sync"
            if n_synced > 0:
                return "◐ Modified"
            return "Not Sync"
        _pstatus = {}
        for _pid, _g in df.groupby("product_id", sort=False):
            _pstatus[_pid] = _product_status(_g["shopify_status"])
        # Created-on date (YYYY-MM-DD) for daily filtering and display
        df["created_date"] = pd.to_datetime(df["created_at"], errors="coerce").dt.strftime("%Y-%m-%d")

        sec("SEARCH & FILTER")
        search = st.text_input("Search", placeholder="Search by SKU, item name or model number...", label_visibility="collapsed")
        f1,f2,f3,f4,f5 = st.columns(5)
        category  = f1.selectbox("Category",     ["All"] + sorted(df["category"].dropna().unique().tolist()))
        sub_vals  = sorted(df["sub_category"].dropna().unique().tolist()) if "sub_category" in df.columns else []
        sub_cat_f = f2.selectbox("Sub-Category", ["All"] + sub_vals)
        brand     = f3.selectbox("Brand",        ["All"] + sorted(df["brand"].dropna().unique().tolist()))
        grade     = f4.selectbox("Grade",        ["All"] + sorted(df["grade"].dropna().unique().tolist()))
        colour    = f5.selectbox("Colour",       ["All"] + sorted(df["colour_code"].dropna().unique().tolist()))
        g1, g2, g3 = st.columns(3)
        pid_opts  = sorted(df["product_id"].dropna().unique().tolist())
        product_f = g1.selectbox("Product ID",     ["All"] + pid_opts)
        shopify_f = g2.selectbox("Shopify Status", ["All", "✅ Sync", "◐ Modified", "Not Sync"])
        date_opts = sorted(df["created_date"].dropna().unique().tolist(), reverse=True)
        created_f = g3.selectbox("Created On", ["All"] + date_opts)

        filtered = df.copy()
        if search:
            mask = filtered[["sku","item_name","model_name"]].fillna("").astype(str).apply(
                lambda col: col.str.contains(search, case=False, regex=False)).any(axis=1)
            filtered = filtered[mask]
        for _col, _val in [("category",category),("sub_category",sub_cat_f),
                            ("brand",brand),("grade",grade),("colour_code",colour),
                            ("product_id",product_f),("created_date",created_f)]:
            if _val != "All" and _col in filtered.columns:
                filtered = filtered[filtered[_col].fillna("") == _val]
        if shopify_f != "All":
            _want = {pid for pid, s in _pstatus.items() if s == shopify_f}
            filtered = filtered[filtered["product_id"].isin(_want)]

        display_df = filtered.copy()
        display_df["Status"] = display_df["quantity"].apply(
            lambda q: "Out of Stock" if q==0 else ("Low Stock" if q<=3 else "In Stock"))

        st.markdown(f'<div class="filter-count">{len(filtered)} variant{"s" if len(filtered)!=1 else ""} found</div>',
                    unsafe_allow_html=True)

        tab_prod, tab_var = st.tabs(["📦  Products", "🎨  Variants"])

        # ── Product view — one row per product (product-level fields) ──────────
        with tab_prod:
            def _agg_status(s):
                return _product_status(pd.Series(list(s)))

            def _s(v):
                return "" if v is None or (isinstance(v, float) and pd.isna(v)) else v

            prod_groups = []
            for pid, g in display_df.groupby("product_id", sort=False):
                first = g.iloc[0]
                try:
                    _imgs = json.loads(first.get("images_json") or "[]")
                    if not isinstance(_imgs, list): _imgs = []
                except Exception:
                    _imgs = []
                try:
                    _specs = json.loads(first.get("specifications_json") or "{}")
                    _specs_str = json.dumps(_specs, ensure_ascii=False) if _specs else ""
                except Exception:
                    _specs_str = ""
                prod_groups.append({
                    "product_id":     pid,
                    "item_name":      _s(first.get("item_name")),
                    "model_name":     _s(first.get("model_name")),
                    "brand":          _s(first.get("brand")),
                    "category":       _s(first.get("category")),
                    "sub_category":   _s(first.get("sub_category")),
                    "capacity":       first.get("capacity"),
                    "capacity_unit":  _s(first.get("capacity_unit")),
                    "variants":       len(g),
                    "total_qty":      int(g["quantity"].sum()),
                    "Shopify Status": _agg_status(g.get("shopify_status", pd.Series(dtype=str)).tolist()),
                    "created_date":   _s(first.get("created_date")),
                    "cover_image":    _imgs[0] if _imgs else "",
                    "image_count":    len(_imgs),
                    "description":    _s(first.get("description")),
                    "about":          _s(first.get("amazon_about_this_item")),
                    "whats_in_box":   _s(first.get("whats_in_the_box")),
                    "specs":          _specs_str,
                    "amazon_asin":    _s(first.get("amazon_asin")),
                    "amazon_title":   _s(first.get("amazon_title")),
                    "amazon_price":   _s(first.get("amazon_price")),
                    "amazon_rating":  _s(first.get("amazon_rating")),
                    "amazon_reviews": _s(first.get("amazon_reviews")),
                    "amazon_availability": _s(first.get("amazon_availability")),
                    "weight_grams":   first.get("weight_grams"),
                    "weight_unit":    _s(first.get("weight_unit")),
                    "packed_length":  first.get("packed_length"),
                    "packed_width":   first.get("packed_width"),
                    "packed_height":  first.get("packed_height"),
                    "source_url":     _s(first.get("source_url")),
                })
            prod_df = pd.DataFrame(prod_groups)

            # Persist product selection across pagination
            sel_set = st.session_state.setdefault("inv_sel_products", set())
            # Drop any stale ids no longer in the current filtered set
            _valid_ids = set(prod_df["product_id"]) if not prod_df.empty else set()
            sel_set &= _valid_ids

            PER_PAGE = 10
            total_products = len(prod_df)
            total_pages = max(1, (total_products + PER_PAGE - 1) // PER_PAGE)
            page_no = st.session_state.get("inv_prod_page", 0)
            page_no = max(0, min(page_no, total_pages - 1))
            st.session_state["inv_prod_page"] = page_no

            # Reserve the top row for the Push button so it reflects the live
            # selection (filled AFTER the editor updates the selection set).
            push_area = st.container()

            st.caption(f"{total_products} product{'s' if total_products != 1 else ''} · "
                       f"page {page_no + 1} of {total_pages}")

            page_slice = prod_df.iloc[page_no * PER_PAGE:(page_no + 1) * PER_PAGE]
            page_pids  = page_slice["product_id"].tolist()

            # Bumping this nonce gives the editor a fresh key so a programmatic
            # select-all / clear is reflected instead of the widget's cached edits.
            _ed_nonce = st.session_state.get("inv_prod_editor_nonce", 0)
            sa1, sa2, _sa3 = st.columns([1.3, 1.3, 4])
            with sa1:
                if st.button("☑ Select all on page", use_container_width=True, key="inv_sel_all"):
                    sel_set.update(page_pids)
                    st.session_state["inv_sel_products"] = sel_set
                    st.session_state["inv_prod_editor_nonce"] = _ed_nonce + 1
                    rerun()
            with sa2:
                if st.button("☐ Clear selection", use_container_width=True, key="inv_sel_clear"):
                    sel_set.clear()
                    st.session_state["inv_sel_products"] = sel_set
                    st.session_state["inv_prod_editor_nonce"] = _ed_nonce + 1
                    rerun()

            page_df = page_slice.copy()
            page_df.insert(0, "Select", [pid in sel_set for pid in page_df["product_id"]])

            edited_prod = st.data_editor(
                page_df, use_container_width=True, hide_index=True,
                key=f"inv_prod_editor_{page_no}_{_ed_nonce}",
                column_config={
                    "Select":        st.column_config.CheckboxColumn("Select", default=False),
                    "product_id":    st.column_config.TextColumn("Product ID", width="medium", disabled=True),
                    "item_name":     st.column_config.TextColumn("Item Name", width="large", disabled=True),
                    "model_name":    st.column_config.TextColumn("Model", disabled=True),
                    "brand":         st.column_config.TextColumn("Brand", disabled=True),
                    "category":      st.column_config.TextColumn("Category", disabled=True),
                    "sub_category":  st.column_config.TextColumn("Sub-Category", disabled=True),
                    "capacity":      st.column_config.NumberColumn("Capacity", disabled=True),
                    "capacity_unit": st.column_config.TextColumn("Unit", disabled=True),
                    "variants":      st.column_config.NumberColumn("Variants", format="%d", width="small", disabled=True),
                    "total_qty":     st.column_config.NumberColumn("Total Qty", format="%d", width="small", disabled=True),
                    "Shopify Status":st.column_config.TextColumn("Shopify Status", disabled=True),
                    "created_date":  st.column_config.TextColumn("Created On", width="small", disabled=True),
                    "cover_image":   st.column_config.ImageColumn("Cover"),
                    "image_count":   st.column_config.NumberColumn("# Imgs", format="%d", width="small", disabled=True),
                    "description":   st.column_config.TextColumn("Description", width="large", disabled=True),
                    "about":         st.column_config.TextColumn("About this item", width="large", disabled=True),
                    "whats_in_box":  st.column_config.TextColumn("What's in box", width="medium", disabled=True),
                    "specs":         st.column_config.TextColumn("Specifications (JSON)", width="large", disabled=True),
                    "amazon_asin":   st.column_config.TextColumn("ASIN", disabled=True),
                    "amazon_title":  st.column_config.TextColumn("Amazon Title", width="large", disabled=True),
                    "amazon_price":  st.column_config.TextColumn("Amazon Price", disabled=True),
                    "amazon_rating": st.column_config.TextColumn("Rating", disabled=True),
                    "amazon_reviews":st.column_config.TextColumn("Reviews", disabled=True),
                    "amazon_availability": st.column_config.TextColumn("Availability", disabled=True),
                    "weight_grams":  st.column_config.NumberColumn("Weight (g)", disabled=True),
                    "weight_unit":   st.column_config.TextColumn("Wt Unit", disabled=True),
                    "packed_length": st.column_config.NumberColumn("Pack L", disabled=True),
                    "packed_width":  st.column_config.NumberColumn("Pack W", disabled=True),
                    "packed_height": st.column_config.NumberColumn("Pack H", disabled=True),
                    "source_url":    st.column_config.LinkColumn("Source URL", disabled=True),
                },
                column_order=["Select", "Shopify Status", "product_id", "item_name", "model_name",
                              "brand", "category", "sub_category", "capacity", "capacity_unit",
                              "variants", "total_qty", "created_date", "cover_image", "image_count",
                              "description", "about", "whats_in_box", "specs",
                              "amazon_asin", "amazon_title", "amazon_price", "amazon_rating",
                              "amazon_reviews", "amazon_availability",
                              "weight_grams", "weight_unit", "packed_length", "packed_width",
                              "packed_height", "source_url"],
            )

            # Merge this page's checkbox state back into the persistent selection
            for _, _r in edited_prod.iterrows():
                if _r["Select"]:
                    sel_set.add(_r["product_id"])
                else:
                    sel_set.discard(_r["product_id"])
            st.session_state["inv_sel_products"] = sel_set

            # ── Push to Shopify + Exports (top row, below filters) ────────────
            with push_area:
                pc1, pc2, pc3, pc4 = st.columns([1.5, 1, 1, 1.3])
                with pc1:
                    n_sel = len(sel_set)
                    push_label = f"🚀 Push to Shopify ({n_sel})" if n_sel else "🚀 Push to Shopify"
                    if st.button(push_label, type="primary", disabled=(n_sel == 0),
                                 use_container_width=True, key="inv_push_btn"):
                        # Gather the FULL variant set of each selected product from the
                        # unfiltered df — productSet upserts the whole product, so a
                        # partial set would drop the omitted variants on Shopify.
                        push_rows = df[df["product_id"].isin(sel_set)].to_dict(orient="records")
                        st.session_state["inv_push_rows"] = push_rows
                        rerun()
                with pc2:
                    st.download_button("⬇ CSV", filtered.to_csv(index=False).encode("utf-8"),
                                       "inventory_export.csv", "text/csv",
                                       use_container_width=True, key="inv_dl_csv")
                with pc3:
                    _buf = _io.BytesIO()
                    filtered.to_excel(_buf, index=False, engine="openpyxl")
                    st.download_button("⬇ Excel", _buf.getvalue(), "inventory_export.xlsx",
                                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                       use_container_width=True, key="inv_dl_xlsx")
                with pc4:
                    _shop_df = build_shopify_rows(filtered.to_dict(orient="records"))
                    st.download_button("⬇ Shopify CSV", _shop_df.to_csv(index=False).encode("utf-8"),
                                       "shopify_import.csv", "text/csv",
                                       use_container_width=True, key="inv_dl_shopify")
                st.caption(f"{n_sel} product(s) selected" if n_sel
                           else "Tick products below, then Push to Shopify.")

            # ── Pagination controls ───────────────────────────────────────────
            if total_pages > 1:
                nav_prev, nav_mid, nav_next = st.columns([1, 3, 1])
                with nav_prev:
                    if page_no > 0 and st.button("← Previous", key="inv_prod_prev"):
                        st.session_state["inv_prod_page"] = page_no - 1
                        rerun()
                with nav_next:
                    if page_no < total_pages - 1 and st.button("Next →", key="inv_prod_next"):
                        st.session_state["inv_prod_page"] = page_no + 1
                        rerun()

        # ── Variant view — one row per SKU (variant-level fields) ──────────────
        with tab_var:
            def _colours(v):
                try:
                    cs = json.loads(v or "[]")
                    return ", ".join(cs) if isinstance(cs, list) else ""
                except Exception:
                    return ""
            var_src = display_df.copy()
            var_src["colours"] = var_src.get("colours_json", pd.Series([""] * len(var_src))).apply(_colours)
            # DatetimeColumn needs real datetimes, not ISO strings, or it renders blank.
            if "shopify_pushed_at" in var_src.columns:
                var_src["shopify_pushed_at"] = pd.to_datetime(var_src["shopify_pushed_at"], errors="coerce")
            _vstate = {"synced": "✅ Sync", "modified": "◐ Modified", "failed": "❌ Failed"}
            var_src["shopify_state"] = var_src["shopify_status"].apply(
                lambda s: _vstate.get(str(s).strip().lower(), "Not started") if pd.notna(s) and str(s).strip() else "Not started")
            VAR_COLS = ["product_id","sku","grade","colours","colour_code","Status","quantity",
                        "mrp","purchase_price","selling_price","osp","lot_number","variant_image",
                        "shopify_state","shopify_pushed_at","created_date","created_by",
                        "modified_by","updated_at"]
            var_df = var_src[[c for c in VAR_COLS if c in var_src.columns]]
            st.caption(f"{len(var_df)} variant{'s' if len(var_df)!=1 else ''}")
            st.dataframe(var_df, use_container_width=True, hide_index=True, column_config={
                "product_id":     st.column_config.TextColumn("Product ID", width="medium"),
                "sku":            st.column_config.TextColumn("Variant SKU", width="medium"),
                "grade":          st.column_config.TextColumn("Grade"),
                "colours":        st.column_config.TextColumn("Colours"),
                "colour_code":    st.column_config.TextColumn("Colour Code"),
                "Status":         st.column_config.TextColumn("Stock Status"),
                "quantity":       st.column_config.NumberColumn("Qty", format="%d", width="small"),
                "mrp":            st.column_config.NumberColumn("MRP (₹)", format="%.0f"),
                "purchase_price": st.column_config.NumberColumn("Purchase (₹)", format="%.0f"),
                "selling_price":  st.column_config.NumberColumn("Sell Price (₹)", format="%.0f"),
                "osp":            st.column_config.NumberColumn("OSP (₹)", format="%.0f"),
                "lot_number":     st.column_config.TextColumn("LOT"),
                "variant_image":  st.column_config.ImageColumn("Variant Img"),
                "shopify_state":  st.column_config.TextColumn("Shopify Status"),
                "shopify_pushed_at": st.column_config.DatetimeColumn("Pushed At", format="DD MMM YYYY, HH:mm"),
                "created_date":   st.column_config.TextColumn("Created On", width="small"),
                "created_by":     st.column_config.TextColumn("Created By"),
                "modified_by":    st.column_config.TextColumn("Modified By"),
                "updated_at":     st.column_config.DatetimeColumn("Last Updated", format="DD MMM YYYY"),
            })

        if is_admin():
            sec("EDIT PRODUCT CONTENT")
            sel_ids = st.session_state.get("inv_sel_products", set())
            if len(sel_ids) == 0:
                st.info("Tick exactly **one** product in the table above to edit its content here. "
                        "(Tick several only when you want to push them together.)")
            elif len(sel_ids) > 1:
                st.info(f"**{len(sel_ids)} products selected** — you can push them all to Shopify, "
                        "but content is edited one product at a time. "
                        "Leave just one ticked to edit it here.")
            else:
                sel_pid = next(iter(sel_ids))
                grp     = df[df["product_id"] == sel_pid]
                if grp.empty:
                    st.info("Selected product is not in the current view.")
                else:
                    rep       = grp.iloc[0].to_dict()
                    row_ids   = [int(x) for x in grp["id"].tolist()]
                    # Locked once ANY variant is on Shopify (Sync or Modified).
                    is_synced = _pstatus.get(sel_pid) != "Not Sync"

                    st.markdown(f"**Editing:** `{sel_pid}` — {rep.get('item_name','')}")
                    if is_synced:
                        st.warning("🔒 This product already has variants on Shopify — content is locked. "
                                   "Only products with no synced variants can be edited.")
                    else:
                        st.caption(f"Content is shared across {len(row_ids)} variant(s) of this product.")

                    def _clean(v):
                        return "" if v is None or (isinstance(v, float) and pd.isna(v)) else v

                    with st.form("edit_product_content"):
                        pb1, pb2, pb3 = st.columns([2, 1, 1])
                        ed_model = pb1.text_input("Model name", _clean(rep.get("model_name")),
                                                  disabled=is_synced)
                        _cap_val = rep.get("capacity")
                        ed_cap   = pb2.number_input("Capacity", min_value=0, step=1,
                                                    value=int(_cap_val) if pd.notna(_cap_val) and _cap_val else 0,
                                                    disabled=is_synced)
                        _unit_opts2 = ["Select","L","mL","kg","g","GB","TB","W","kW","Ah","mAh","pieces","other"]
                        _cur_unit   = _clean(rep.get("capacity_unit")) or "Select"
                        ed_unit  = pb3.selectbox("Unit", _unit_opts2,
                                                 index=_unit_opts2.index(_cur_unit) if _cur_unit in _unit_opts2 else 0,
                                                 disabled=is_synced)
                        ed_desc  = st.text_area("Description", _clean(rep.get("description")),
                                                height=140, disabled=is_synced)
                        ed_about = st.text_area("About this item", _clean(rep.get("amazon_about_this_item")),
                                                height=120, disabled=is_synced)
                        ed_witb  = st.text_area("What's inside the box", _clean(rep.get("whats_in_the_box")),
                                                height=90, disabled=is_synced)
                        try:
                            _specs_def = json.dumps(json.loads(rep.get("specifications_json") or "{}"), indent=2)
                        except Exception:
                            _specs_def = "{}"
                        ed_specs = st.text_area("Specifications (JSON)", _specs_def,
                                                height=150, disabled=is_synced,
                                                help='Must be a JSON object, e.g. {"Capacity": "8 kg", "Colour": "White"}')
                        ed_src   = st.text_input("Source / Amazon URL", _clean(rep.get("source_url")),
                                                 disabled=is_synced)
                        ed_shopcat = st.text_input("Shopify Category (leaf only)",
                                                 _clean(rep.get("shopify_category")), disabled=is_synced,
                                                 help="e.g. Washing Machines — resolved to the full Shopify taxonomy on push")
                        save_c   = st.form_submit_button("Save product content", type="primary",
                                                         disabled=is_synced)

                    if save_c and not is_synced:
                        try:
                            if ed_src.strip() and urlparse(clean_amazon_url(ed_src)).scheme not in ("http","https"):
                                raise ValueError("Source URL must start with http:// or https://")
                            try:
                                specs = json.loads(ed_specs or "{}")
                            except json.JSONDecodeError as je:
                                raise ValueError(f"Specifications must be valid JSON — {je}")
                            if not isinstance(specs, dict):
                                raise ValueError("Specifications must be a JSON object (use { } with key/value pairs).")
                            update_product_content(row_ids, {
                                "model_name": ed_model.strip(),
                                "capacity": int(ed_cap) if ed_cap else None,
                                "capacity_unit": ed_unit if ed_unit != "Select" else None,
                                "description": ed_desc,
                                "amazon_about_this_item": ed_about,
                                "whats_in_the_box": ed_witb.strip() or None,
                                "specifications": specs,
                                "source_url": clean_amazon_url(ed_src) if ed_src.strip() else None,
                                "shopify_category": ed_shopcat.strip() or None,
                            }, user)
                            st.success(f"Updated content for {len(row_ids)} variant(s) of {sel_pid}.")
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

    def _ac_field(col, key_name, label, placeholder):
        cur_text  = st.session_state.get(f"{key_name}_{fc}", "")
        all_suggs = inventory_suggestions(key_name)   # full list — JS filters locally
        with col:
            val = autocomplete_component(
                label=label, placeholder=placeholder,
                value=cur_text, suggestions=all_suggs,
                key=f"{key_name}_ac_{fc}",
                default={"text": "", "selected": False},
            )
        if isinstance(val, dict) and val.get("selected"):
            new_text = val.get("text", "")
            if new_text != cur_text:
                st.session_state[f"{key_name}_{fc}"] = new_text
                rerun()
        # Capture text typed directly even when not selected from dropdown
        if isinstance(val, dict) and val.get("text") and not val.get("selected"):
            st.session_state[f"{key_name}_{fc}"] = val["text"]
        return st.session_state.get(f"{key_name}_{fc}", "")

    item_name  = _ac_field(ai1, "item_name",  "Item Name *", "e.g. Washing Machine, Air Cooler")
    model_name = _ac_field(ai2, "model_name", "Model Name",  "e.g. Magic Clean 8kg, MAGCLE80")

    # Shopify category (leaf) — single mandatory dropdown of last-node names.
    shopify_category, shopify_category_id = "", ""
    if taxonomy_count() == 0:
        st.warning("Shopify taxonomy isn't synced yet — an admin can sync it in **Manage → Shopify Taxonomy**.")
    else:
        _names, _name_map = _taxonomy_options()
        _TAX_PLACEHOLDER = "— Select Shopify category * —"
        _sel = st.selectbox("Shopify Category (last node) *", [_TAX_PLACEHOLDER] + _names,
                            key=f"shopcat_{fc}",
                            help="Type to filter. The full taxonomy path is sent to Shopify on push.")
        if _sel != _TAX_PLACEHOLDER:
            shopify_category_id, _full = _name_map[_sel]
            shopify_category = _sel
            st.caption(f"✅ {_full}")

    # ── PRODUCT CLASSIFICATION ────────────────────────────────────────────────
    sec("PRODUCT CLASSIFICATION")
    ac1, ac2, ac3, ac4, ac5 = st.columns(5)
    category_name   = ac1.selectbox("Category *",   option_labels(cats),  key=f"cat_{fc}")
    selected_cat_id = selected_id(cats, category_name)
    sub_cats        = list_sub_categories(category_id=selected_cat_id, active_only=True) if selected_cat_id else []
    if not sub_cats:
        sub_cat_name = ac2.selectbox(
            "Sub-category",
            ["— select category first —" if not selected_cat_id else "— none configured —"],
            disabled=True, key=f"subcat_{fc}")
    else:
        sub_cat_name = ac2.selectbox("Sub-category", ["Select"] + [r["name"] for r in sub_cats],
                                     key=f"subcat_{fc}")
    brand_name    = ac3.selectbox("Brand *",      option_labels(brands), key=f"brand_{fc}")
    capacity      = ac4.number_input("Capacity",  min_value=0, step=1, value=0, key=f"cap_{fc}")
    capacity_unit = ac5.selectbox("Capacity Unit",
                                  ["Select","L","mL","kg","g","GB","TB","W","kW","Ah","mAh","pieces","other"],
                                  key=f"unit_{fc}")

    # ── VARIANT CLASSIFICATION ────────────────────────────────────────────────
    sec("VARIANT CLASSIFICATION")

    if f"vrow_ids_{fc}" not in st.session_state:
        st.session_state[f"vrow_ids_{fc}"] = [0]
        st.session_state[f"vrow_nxt_{fc}"] = 1

    vrow_ids = st.session_state[f"vrow_ids_{fc}"]

    for row_idx, rid in enumerate(list(vrow_ids)):
        if row_idx > 0:
            st.markdown('<hr style="margin:0.5rem 0 0.25rem;border-color:#e2e8f0">', unsafe_allow_html=True)
        vh_l, vh_r = st.columns([9, 1])
        vh_l.markdown(
            f'<div style="font-size:0.72rem;font-weight:700;color:#2563eb;'
            f'text-transform:uppercase;letter-spacing:0.08em;margin-bottom:0.15rem">'
            f'Variant {row_idx + 1}</div>',
            unsafe_allow_html=True)
        if len(vrow_ids) > 1:
            if vh_r.button("✕ Remove", key=f"v_rm_{rid}_{fc}"):
                st.session_state[f"vrow_ids_{fc}"].remove(rid)
                rerun()
        va1, va2, va3 = st.columns([1.5, 1.5, 3])
        va1.text_input("LOT No",   placeholder="LOT-001",           key=f"v_lot_{rid}_{fc}")
        va2.selectbox("Grade",     option_labels(grades),            key=f"v_grade_{rid}_{fc}")
        va3.multiselect("Colours", [r["name"] for r in colours],     key=f"v_cols_{rid}_{fc}")
        vb1, vb2, vb3, vb4, vb5 = st.columns(5)
        vb1.number_input("Quantity *",   min_value=1,   step=1,   value=1,   key=f"v_qty_{rid}_{fc}")
        vb2.number_input("MRP (₹)",      min_value=0.0, step=1.0, value=0.0, key=f"v_mrp_{rid}_{fc}")
        vb3.number_input("Purchase (₹)", min_value=0.0, step=1.0, value=0.0, key=f"v_pur_{rid}_{fc}")
        vb4.number_input("Selling (₹)",  min_value=0.0, step=1.0, value=0.0, key=f"v_sell_{rid}_{fc}")
        vb5.number_input("OSP (₹)",      min_value=0.0, step=1.0, value=0.0, key=f"v_osp_{rid}_{fc}")
        vc1, vc2 = st.columns(2)
        vc1.text_input("HS Code", placeholder="6-digit (optional)", max_chars=10, key=f"v_hs_{rid}_{fc}")
        vc2.selectbox("Warehouse Location *", ["BS01-Retail", "BS01-WH01", "BS01-WH02"],
                      index=1, key=f"v_wh_{rid}_{fc}")

    st.markdown('<div style="height:0.25rem"></div>', unsafe_allow_html=True)
    if st.button("＋  Add variant", key=f"v_add_{fc}"):
        nxt = st.session_state[f"vrow_nxt_{fc}"]
        st.session_state[f"vrow_ids_{fc}"].append(nxt)
        st.session_state[f"vrow_nxt_{fc}"] = nxt + 1
        rerun()

    # ── SOURCE / AMAZON ───────────────────────────────────────────────────────
    sec("SOURCE / AMAZON")
    st.markdown(
        '<div class="amazon-header">🛒  Paste an Amazon product URL — the tool will fetch title, description, specs and images automatically.</div>',
        unsafe_allow_html=True)
    source_url = st.text_input("Amazon / Source URL", placeholder="https://www.amazon.in/dp/...", key=f"src_{fc}")

    st.markdown('<div style="height:0.5rem"></div>', unsafe_allow_html=True)

    # Collect variant inputs before button (widgets already rendered above)
    _vrow_ids_now = st.session_state.get(f"vrow_ids_{fc}", [0])
    _variant_inputs = [
        {
            "lot_number":       (st.session_state.get(f"v_lot_{_r}_{fc}",  "") or "").strip(),
            "grade_name":       st.session_state.get(f"v_grade_{_r}_{fc}", "Select"),
            "selected_colours": st.session_state.get(f"v_cols_{_r}_{fc}",  []) or [],
            "quantity":         int(st.session_state.get(f"v_qty_{_r}_{fc}",  1) or 1),
            "mrp":              float(st.session_state.get(f"v_mrp_{_r}_{fc}", 0.0) or 0.0),
            "purchase":         float(st.session_state.get(f"v_pur_{_r}_{fc}", 0.0) or 0.0),
            "selling":          float(st.session_state.get(f"v_sell_{_r}_{fc}", 0.0) or 0.0),
            "osp":              float(st.session_state.get(f"v_osp_{_r}_{fc}", 0.0) or 0.0),
            "hs_code":          (st.session_state.get(f"v_hs_{_r}_{fc}", "") or "").strip(),
            "warehouse_location": st.session_state.get(f"v_wh_{_r}_{fc}", "BS01-WH01"),
        }
        for _r in _vrow_ids_now
    ]

    if st.button("🔍  Fetch Amazon details & preview SKU", type="primary",
                 disabled=bool(st.session_state.get("add_preview"))):
        if not item_name.strip() or category_name == "Select" or brand_name == "Select":
            st.error("Item name, category and brand are required before fetching.")
        elif not shopify_category.strip():
            st.error("Shopify Category is required (enter the leaf, e.g. 'Washing Machines').")
        elif source_url and urlparse(clean_amazon_url(source_url)).scheme not in ("http","https"):
            st.error("Source URL must start with http:// or https://")
        else:
            try:
                for _idx, _vi in enumerate(_variant_inputs):
                    validate_prices(_vi["mrp"], _vi["selling"], _vi["purchase"],
                                    prefix=f"Variant {_idx + 1}: ")
                    _hs = (_vi.get("hs_code") or "").strip()
                    if _hs and not re.fullmatch(r"\d{6,13}", _hs):
                        raise ValueError(f"Variant {_idx + 1}: HS Code must be 6–13 digits (numbers only).")

                # Guard against duplicate variant rows (same grade + colours): they
                # generate the same SKU and would silently merge/double-count.
                _combo_seen = set()
                for _idx, _vi in enumerate(_variant_inputs):
                    _combo = (_vi["grade_name"], tuple(sorted(c.casefold() for c in _vi["selected_colours"])))
                    if _combo in _combo_seen:
                        raise ValueError(f"Variant {_idx + 1} duplicates another row's grade + colour — "
                                         "combine them into one row or change the grade/colour.")
                    _combo_seen.add(_combo)

                sub_cat_id = selected_id(sub_cats, sub_cat_name) if sub_cats and sub_cat_name not in (
                    "Select", "— none configured —", "— select category first —") else None

                all_variants = []
                for _vi in _variant_inputs:
                    _g_id = selected_id(grades, _vi["grade_name"]) if _vi["grade_name"] != "Select" else None
                    _vlist = preview_variants(
                        item_name.strip(), model_name.strip(),
                        selected_cat_id, sub_cat_id,
                        selected_id(brands, brand_name),
                        _g_id, _vi["selected_colours"], _vi["quantity"], user)
                    _v = _vlist[0]
                    _v["grade_id"]       = _g_id
                    _v["grade_name"]     = _vi["grade_name"] if _vi["grade_name"] != "Select" else "—"
                    _v["mrp"]            = _vi["mrp"] or None
                    _v["purchase_price"] = _vi["purchase"] or None
                    _v["selling_price"]  = _vi["selling"] or None
                    _v["lot_number"]     = _vi["lot_number"] or None
                    _v["osp"]            = _vi["osp"] or None
                    _v["hs_code"]        = _vi.get("hs_code") or None
                    _v["warehouse_location"] = _vi.get("warehouse_location") or None
                    all_variants.append(_v)

                # Default amazon data
                amazon_data = {"amazon_asin":"","amazon_title":"","amazon_price":"","amazon_rating":"",
                               "amazon_reviews":"","amazon_availability":"","description":"",
                               "amazon_about_this_item":"","specifications":{},"images":[],
                               "whats_in_the_box":""}
                draft_capacity      = int(capacity) if capacity else None
                draft_capacity_unit = capacity_unit if capacity_unit != "Select" else None

                # If the PRODUCT already exists (by handle), we already have all its
                # content — reuse it and skip the Amazon fetch entirely.
                _handle = all_variants[0].get("product_handle") if all_variants else None
                _existing_product = get_product_by_handle(_handle)
                product_exists = bool(_existing_product)
                draft_shopify_category = (shopify_category or "").strip() or None
                draft_shopify_category_id = (shopify_category_id or "").strip() or None
                if product_exists and _existing_product.get("shopify_category"):
                    draft_shopify_category = _existing_product.get("shopify_category")
                    draft_shopify_category_id = _existing_product.get("shopify_category_id")

                if product_exists:
                    _ex = _existing_product
                    _specs = {}
                    try: _specs = json.loads(_ex.get("specifications_json") or "{}")
                    except Exception: pass
                    _images = []
                    try: _images = json.loads(_ex.get("images_json") or "[]")
                    except Exception: pass
                    amazon_data = {
                        "amazon_asin":            _ex.get("amazon_asin") or "",
                        "amazon_title":           _ex.get("amazon_title") or "",
                        "amazon_price":           _ex.get("amazon_price") or "",
                        "amazon_rating":          _ex.get("amazon_rating") or "",
                        "amazon_reviews":         _ex.get("amazon_reviews") or "",
                        "amazon_availability":    _ex.get("amazon_availability") or "",
                        "description":            _ex.get("description") or "",
                        "amazon_about_this_item": _ex.get("amazon_about_this_item") or "",
                        "specifications":         _specs,
                        "images":                 _images,
                        "whats_in_the_box":       _ex.get("whats_in_the_box") or "",
                    }
                    draft_capacity      = _ex.get("capacity")      if _ex.get("capacity") is not None else draft_capacity
                    draft_capacity_unit = _ex.get("capacity_unit") or draft_capacity_unit
                    _n_new = sum(1 for _v in all_variants if _v.get("current_quantity", 0) == 0)
                    st.success(
                        f"Existing product found ({_handle}) — reusing its saved details. "
                        + (f"Upload one image for each of the {_n_new} new variant(s) below."
                           if _n_new else "All selected variants already exist; just confirm the stock update."))
                else:
                    _any_new = any(_v.get("current_quantity", 0) == 0 for _v in all_variants)
                    if source_url and _any_new:
                        try:
                            with st.spinner("Fetching product details from Amazon — this may take up to 60 seconds…"):
                                raw_amazon = fetch_amazon_product(clean_amazon_url(source_url), proxy_api_key=_SCRAPERAPI_KEY or None)
                            scrape_err = raw_amazon.get("error", "")
                            if scrape_err:
                                st.warning(f"Amazon fetch issue: {scrape_err} — You can enter details manually.")
                            else:
                                amazon_data = normalize_amazon_data(raw_amazon)
                                st.success("Amazon details fetched. Review and edit below before saving.")
                        except Exception as _scrape_err:
                            st.warning(f"Amazon fetch failed: {_scrape_err} — You can enter details manually.")

                st.session_state.add_preview = {
                    "variants": all_variants,
                    "item_name": item_name.strip(), "model_name": model_name.strip(),
                    "product_handle": _handle,
                    "product_exists": product_exists,
                    "category_id": selected_cat_id, "sub_category_id": sub_cat_id,
                    "brand_id": selected_id(brands, brand_name),
                    "capacity": draft_capacity, "capacity_unit": draft_capacity_unit,
                    "shopify_category": draft_shopify_category,
                    "shopify_category_id": draft_shopify_category_id,
                    "source_url": clean_amazon_url(source_url) if source_url else None,
                    **amazon_data,
                }
            except Exception as e:
                st.error(str(e))

    # ── Review & save ─────────────────────────────────────────────────────────
    draft = st.session_state.get("add_preview")
    if draft:
        st.markdown('<hr style="margin:2rem 0 1rem;border-color:#e2e8f0">', unsafe_allow_html=True)
        variants     = draft["variants"]
        any_conflict = any(v.get("conflict", False) for v in variants)
        first_sku    = variants[0]["sku"]
        product_exists = bool(draft.get("product_exists"))

        # ── SKU summary tiles ─────────────────────────────────────────────────
        for _v in variants:
            if _v.get("conflict"):
                _icon, _msg = "⛔", "Attribute conflict — resolve and re-fetch"
                _bg, _border = "#fef2f2", "#fca5a5"
            elif _v.get("current_quantity", 0) > 0:
                _icon = "📦"
                _msg  = f"Add {_v['quantity_to_add']} to existing stock ({_v['current_quantity']} units)"
                _bg, _border = "#eff6ff", "#93c5fd"
            else:
                _icon = "✅"
                _msg  = f"New SKU — {_v['quantity_to_add']} unit(s)"
                _bg, _border = "#f0fdf4", "#86efac"
            st.markdown(
                f'<div style="background:{_bg};border:1px solid {_border};border-radius:8px;'
                f'padding:0.55rem 1rem;margin-bottom:0.3rem;display:flex;align-items:center;gap:0.75rem">'
                f'<span style="font-size:1.1rem">{_icon}</span>'
                f'<span style="font-family:monospace;font-weight:700;font-size:0.9rem">{_v["sku"]}</span>'
                f'<span style="font-size:0.82rem;color:#475569">{_msg}</span>'
                f'</div>',
                unsafe_allow_html=True)

        if any_conflict:
            st.error("⛔ One or more SKUs have attribute conflicts and cannot be saved.")

        # ── Product details ───────────────────────────────────────────────────
        sec("REVIEW — PRODUCT DETAILS")
        if product_exists:
            st.caption("🔒 Existing product — identity and shared details are locked; reusing saved data.")
        rv1, rv2 = st.columns(2)
        review_item_name  = rv1.text_input("Item name *", value=draft["item_name"],  key=f"rv_item_{first_sku}", disabled=product_exists)
        review_model_name = rv2.text_input("Model name",  value=draft["model_name"], key=f"rv_model_{first_sku}", disabled=product_exists)
        rc1, rc2 = st.columns(2)
        _unit_opts    = ["Select","L","mL","kg","g","GB","TB","W","kW","Ah","mAh","pieces","other"]
        _exist_unit   = draft.get("capacity_unit") or "Select"
        review_capacity  = rc1.number_input("Capacity", min_value=0, step=1,
                                            value=int(draft.get("capacity") or 0),
                                            key=f"rv_cap_{first_sku}", disabled=product_exists)
        review_cap_unit  = rc2.selectbox("Capacity Unit", _unit_opts,
                                         index=_unit_opts.index(_exist_unit) if _exist_unit in _unit_opts else 0,
                                         key=f"rv_unit_{first_sku}", disabled=product_exists)
        review_source_url = st.text_input("Source URL", value=draft.get("source_url") or "",
                                          key=f"rv_src_{first_sku}", disabled=product_exists)
        review_shopify_category = st.text_input("Shopify Category (last node)",
                                          value=draft.get("shopify_category") or "",
                                          key=f"rv_shopcat_{first_sku}", disabled=True,
                                          help="Chosen in the form above; the full taxonomy path is sent on push.")

        # ── Per-variant review cards ──────────────────────────────────────────
        sec("REVIEW — VARIANT DETAILS")
        _rv_edits = []
        _variant_uploads = {}   # variant_idx → uploaded image (existing-product flow)
        for _i, _v in enumerate(variants):
            _is_conflict = _v.get("conflict", False)
            _card_bg     = "#fef2f2" if _is_conflict else "#f8fafc"
            st.markdown(
                f'<div style="background:{_card_bg};border:1px solid #e2e8f0;border-radius:10px;'
                f'padding:0.75rem 1rem 0.25rem;margin-bottom:0.5rem">'
                f'<div style="font-size:0.7rem;font-weight:700;color:#2563eb;text-transform:uppercase;'
                f'letter-spacing:0.08em;margin-bottom:0.4rem">'
                f'Variant {_i + 1} — {_v["sku"]}'
                f'<span style="font-weight:400;color:#64748b;margin-left:0.75rem">'
                f'Grade: {_v.get("grade_name","—")} · Colours: {_v.get("colours","—")}</span>'
                f'</div></div>',
                unsafe_allow_html=True)
            _c1, _c2 = st.columns(2)
            _rv_lot = _c1.text_input("LOT No",   value=_v.get("lot_number") or "", key=f"rv_lot_{_i}_{first_sku}")
            _rv_qty = _c2.number_input("Quantity", min_value=1, step=1,
                                       value=int(_v.get("quantity_to_add") or 1),
                                       key=f"rv_qty_{_i}_{first_sku}")
            _p1, _p2, _p3, _p4 = st.columns(4)
            _rv_mrp  = _p1.number_input("MRP (₹)",      min_value=0.0, step=1.0, format="%.0f",
                                        value=float(_v.get("mrp") or 0),
                                        key=f"rv_mrp_{_i}_{first_sku}")
            _rv_pur  = _p2.number_input("Purchase (₹)", min_value=0.0, step=1.0, format="%.0f",
                                        value=float(_v.get("purchase_price") or 0),
                                        key=f"rv_pur_{_i}_{first_sku}")
            _rv_sell = _p3.number_input("Selling (₹)",  min_value=0.0, step=1.0, format="%.0f",
                                        value=float(_v.get("selling_price") or 0),
                                        key=f"rv_sel_{_i}_{first_sku}")
            _rv_osp  = _p4.number_input("OSP (₹)",      min_value=0.0, step=1.0, format="%.0f",
                                        value=float(_v.get("osp") or 0),
                                        key=f"rv_osp_{_i}_{first_sku}")
            # Existing product → a per-variant photo is only needed for COLOUR variants.
            # Grade-only variants (no colour) reuse the shared product image.
            if product_exists:
                _has_colour = str(_v.get("colours", "—")).strip() not in ("", "—") \
                              or str(_v.get("colour_code", "—")).strip() not in ("", "—")
                if _v.get("current_quantity", 0) > 0:
                    st.caption("📦 Existing variant — image unchanged, just adding stock.")
                elif not _has_colour:
                    st.caption("🎞 Grade-only variant — reuses the shared product image (no separate photo needed).")
                else:
                    _vimg_key = f"rv_vimg_{_i}_{first_sku}"
                    _has_img  = st.session_state.get(_vimg_key) is not None
                    st.markdown(
                        f'<div style="background:{"#ecfdf5" if _has_img else "#fff7ed"};'
                        f'border:1px solid {"#6ee7b7" if _has_img else "#fb923c"};border-radius:8px;'
                        f'padding:0.5rem 0.8rem;margin:0.3rem 0;font-size:0.82rem;font-weight:700;'
                        f'color:{"#065f46" if _has_img else "#9a3412"}">'
                        f'{"✅ Image added for this colour variant" if _has_img else "📷 Colour variant — upload a photo below"}'
                        f'</div>', unsafe_allow_html=True)
                    _up = st.file_uploader(f"Image for Variant {_i + 1} ({_v['sku']}) *",
                                           type=["jpg","jpeg","png","webp"],
                                           key=_vimg_key)
                    if _up is not None:
                        st.image(_up, width=140)
                        _variant_uploads[_i] = _up
            _rv_edits.append({
                "lot_number":     _rv_lot, "quantity_to_add": int(_rv_qty),
                "mrp":            _rv_mrp or None, "purchase_price": _rv_pur or None,
                "selling_price":  _rv_sell or None, "osp": _rv_osp or None,
            })

        # ── Amazon / product content ──────────────────────────────────────────
        sec("REVIEW — PRODUCT CONTENT" + ("  ·  reused from existing product (read-only)" if product_exists else ""))
        ra1, ra2 = st.columns(2)
        review_asin  = ra1.text_input("ASIN",         value=draft.get("amazon_asin",""),  key=f"rv_asin_{first_sku}", disabled=product_exists)
        review_title = ra2.text_input("Amazon title", value=draft.get("amazon_title",""), key=f"rv_atitle_{first_sku}", disabled=product_exists)
        ra3, ra4, ra5 = st.columns(3)
        review_amazon_price = ra3.text_input("Amazon price",  value=draft.get("amazon_price",""),  key=f"rv_aprice_{first_sku}", disabled=product_exists)
        review_rating       = ra4.text_input("Rating",        value=draft.get("amazon_rating",""), key=f"rv_rating_{first_sku}", disabled=product_exists)
        review_reviews      = ra5.text_input("Reviews count", value=draft.get("amazon_reviews",""),key=f"rv_reviews_{first_sku}", disabled=product_exists)
        review_avail        = st.text_input("Availability",   value=draft.get("amazon_availability",""), key=f"rv_avail_{first_sku}", disabled=product_exists)
        review_desc         = st.text_area("Description",     value=draft.get("description",""),   height=140, key=f"rv_desc_{first_sku}", disabled=product_exists)
        review_about        = st.text_area("About this item", value=draft.get("amazon_about_this_item",""), height=120, key=f"rv_about_{first_sku}", disabled=product_exists)
        specs_text          = st.text_area("Specifications (JSON)",
                                           value=json.dumps(draft.get("specifications",{}), ensure_ascii=False, indent=2),
                                           height=160, key=f"rv_specs_{first_sku}", disabled=product_exists)
        review_whats_in_box = st.text_area("What's inside the box", value=draft.get("whats_in_the_box",""), height=80, key=f"rv_witb_{first_sku}", disabled=product_exists)

        _vimgs_key = f"up_vimgs_{first_sku}"
        if not product_exists:
            sec("REVIEW — PRODUCT IMAGES")
            st.caption("Drag to reorder · First image is the cover · Hover a tile to remove · Click 📁 Add image to upload a local photo")

            # Maintain an image pool (Amazon images + locally uploaded) in session_state
            _pool_key  = f"img_pool_{first_sku}"
            _ckey_key  = f"img_ckey_{first_sku}"
            if _pool_key not in st.session_state:
                st.session_state[_pool_key]  = list(draft.get("images", []))
                st.session_state[_ckey_key]  = first_sku
                st.session_state[_vimgs_key] = {}  # variant_idx → image_url

            # Build variant labels before the component so we can pass them as context
            _var_labels = [
                f"Variant {_vi + 1} — {_vv.get('grade_name', '—')}"
                + (f" — {_vv.get('colours', '—')}" if _vv.get("colours", "—") != "—" else "")
                for _vi, _vv in enumerate(variants)
            ]

            edited_images = image_sorter_component(
                images=st.session_state[_pool_key],
                component_key=st.session_state[_ckey_key],
                default=st.session_state[_pool_key],
                key=f"rv_imgs_{st.session_state[_ckey_key]}",  # key changes with ckey → breaks stale action cache
            )
            # Component returns a list on normal edit, or {action:"upload_open"} when the
            # 📁 button is clicked.
            if isinstance(edited_images, dict) and edited_images.get("action") == "upload_open":
                st.session_state["_upload_dialog_ctx"] = {
                    "var_labels": _var_labels,
                    "pool_key":   _pool_key,
                    "vimgs_key":  _vimgs_key,
                    "ckey_key":   _ckey_key,
                    "first_sku":  first_sku,
                    "draft":      draft,
                }
                # Preserve current image list sent with the action
                _imgs_from_action = edited_images.get("images")
                if isinstance(_imgs_from_action, list):
                    st.session_state[_pool_key] = _imgs_from_action
                # Bump ckey NOW so the next render uses a different widget key and won't
                # see the stale {action:"upload_open"} cached value again.
                _dlg_n = st.session_state.get(f"_dlg_n_{first_sku}", 0) + 1
                st.session_state[f"_dlg_n_{first_sku}"] = _dlg_n
                st.session_state[_ckey_key] = f"{first_sku}_dlg{_dlg_n}"
                _upload_image_dialog()
            elif isinstance(edited_images, list):
                st.session_state[_pool_key] = edited_images
        else:
            sec("REVIEW — PRODUCT IMAGES  ·  existing (reused)")
            _ex_imgs = [u for u in (draft.get("images") or []) if isinstance(u, str) and u.strip()]
            if _ex_imgs:
                st.caption(f"{len(_ex_imgs)} existing product image(s) — reused automatically. "
                           "Add a per-variant photo above for each new variant.")
                try:
                    st.image(_ex_imgs[:8], width=90)
                except Exception:
                    st.caption("(image previews unavailable; they'll still attach on push)")
            else:
                st.caption("No shared product images on record — the per-variant photos above will be used.")
            edited_images = None


        st.markdown('<div style="height:0.75rem"></div>', unsafe_allow_html=True)
        col_save, col_cancel, _ = st.columns([1.5, 1, 4])
        if col_save.button("✅  Confirm & save", type="primary", disabled=any_conflict):
            try:
                if not review_item_name.strip():
                    raise ValueError("Item name is required.")
                if review_source_url.strip() and urlparse(clean_amazon_url(review_source_url)).scheme not in ("http","https"):
                    raise ValueError("Source URL must start with http:// or https://")
                specs = json.loads(specs_text or "{}")
                if not isinstance(specs, dict):
                    raise ValueError("Specifications must be a JSON object.")
                for _i, _ed in enumerate(_rv_edits):
                    validate_prices(
                        float(_ed.get("mrp") or 0),
                        float(_ed.get("selling_price") or 0),
                        float(_ed.get("purchase_price") or 0),
                        prefix=f"Variant {_i + 1}: ")

                if product_exists:
                    # Existing product: a photo is required only for NEW COLOUR variants;
                    # grade-only/existing variants reuse the shared product image.
                    _missing, _saved_imgs = [], {}
                    for _i, _v in enumerate(variants):
                        if _v.get("current_quantity", 0) > 0:
                            continue
                        _has_colour = str(_v.get("colours", "—")).strip() not in ("", "—") \
                                      or str(_v.get("colour_code", "—")).strip() not in ("", "—")
                        _up = _variant_uploads.get(_i)
                        if _up is not None:
                            _saved_imgs[_i] = _save_uploaded_image(_up)
                        elif _has_colour:
                            _missing.append(_i + 1)
                    if _missing:
                        raise ValueError(f"Upload an image for the colour variant(s): {', '.join(map(str, _missing))}.")
                    for _i, _v in enumerate(draft["variants"]):
                        if _i < len(_rv_edits):
                            _v.update(_rv_edits[_i])
                        if _i in _saved_imgs:
                            _v["variant_image"] = _saved_imgs[_i]
                    # draft already holds the existing product's content & images.
                else:
                    _saved_vimgs = st.session_state.get(_vimgs_key, {})
                    for _i, _v in enumerate(draft["variants"]):
                        if _i < len(_rv_edits):
                            _v.update(_rv_edits[_i])
                        _v["variant_image"] = _saved_vimgs.get(_i, "")
                    _final_imgs = edited_images if isinstance(edited_images, list) else st.session_state.get(_pool_key, draft.get("images", []))
                    ordered_images = []
                    for img_url in _final_imgs:
                        url = clean_amazon_url(img_url)
                        if url and urlparse(url).scheme in ("http","https") and url not in ordered_images:
                            ordered_images.append(url)
                    draft.update({
                        "item_name": review_item_name.strip(), "model_name": review_model_name.strip(),
                        "capacity": int(review_capacity) if review_capacity else None,
                        "capacity_unit": review_cap_unit if review_cap_unit != "Select" else None,
                        "source_url": clean_amazon_url(review_source_url) if review_source_url.strip() else None,
                        "shopify_category": draft.get("shopify_category"),
                        "shopify_category_id": draft.get("shopify_category_id"),
                        "amazon_asin": review_asin.strip(), "amazon_title": review_title.strip(),
                        "amazon_price": review_amazon_price.strip(), "amazon_rating": review_rating.strip(),
                        "amazon_reviews": review_reviews.strip(), "amazon_availability": review_avail.strip(),
                        "description": review_desc, "amazon_about_this_item": review_about,
                        "specifications": specs, "images": ordered_images,
                        "whats_in_the_box": review_whats_in_box.strip() or None,
                    })

                result = save_inventory_variants(draft, user)
                for msg in result: st.success(msg)
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

    sec("SHOPIFY TAXONOMY")
    _tax_n = taxonomy_count()
    tcol1, tcol2 = st.columns([2, 1])
    tcol1.caption(f"{_tax_n:,} Shopify leaf categories stored — used by the Add Inventory category dropdown."
                  if _tax_n else "No Shopify taxonomy stored yet — sync it so the category dropdown works.")
    with tcol2:
        if st.button("🔄 Sync Shopify taxonomy", use_container_width=True):
            with st.spinner("Downloading Shopify's product taxonomy (~80 MB)…"):
                try:
                    _n = _sync_taxonomy_from_dataset()
                    st.success(f"Synced {_n:,} leaf categories.")
                    rerun()
                except Exception as e:
                    st.error(f"Taxonomy sync failed: {e}")

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


# ── Manage Shopify ────────────────────────────────────────────────────────────
elif page == "Manage Shopify":
    page_header("Manage Shopify", "Review the full Shopify CSV, select SKUs and push to Shopify.")

    # Handle push BEFORE loading inventory so the table shows updated status
    if st.session_state.get("shopify_manage_push_rows"):
        _push_rows = st.session_state.pop("shopify_manage_push_rows")
        _status = _shopify_push_ui(_push_rows)
        if _status["synced_qty_by_id"]:
            mark_synced(_status["synced_qty_by_id"])
        if _status.get("synced_status_only_ids"):
            update_shopify_status(_status["synced_status_only_ids"], "synced")
        if _status["failed_ids"]:
            update_shopify_status(_status["failed_ids"], "failed")

    inv = list_inventory()
    if not inv:
        st.info("No inventory found. Add items via **Add Inventory** first.")
        st.stop()

    df_all = pd.DataFrame(inv)

    sec("FILTERS")
    fc1, fc2, fc3, fc4 = st.columns(4)
    search_q = fc1.text_input("Search (SKU / item / brand)", placeholder="Type to filter…", key="sm_search")
    grade_opts  = ["All"] + sorted(df_all["grade"].dropna().unique().tolist())  if "grade"  in df_all.columns else ["All"]
    brand_opts  = ["All"] + sorted(df_all["brand"].dropna().unique().tolist())  if "brand"  in df_all.columns else ["All"]
    status_opts = ["All", "✅ Synced", "❌ Failed", "— Not pushed"]
    sel_grade  = fc2.selectbox("Grade",           grade_opts,  key="sm_grade")
    sel_brand  = fc3.selectbox("Brand",           brand_opts,  key="sm_brand")
    sel_status = fc4.selectbox("Shopify Status",  status_opts, key="sm_status")

    filtered = df_all.copy()
    if search_q:
        q = search_q.casefold()
        mask = (
            filtered["sku"].astype(str).str.casefold().str.contains(q, na=False)
            | filtered["item_name"].astype(str).str.casefold().str.contains(q, na=False)
            | filtered["brand"].astype(str).str.casefold().str.contains(q, na=False)
        )
        filtered = filtered[mask]
    if sel_grade != "All":
        filtered = filtered[filtered["grade"] == sel_grade]
    if sel_brand != "All":
        filtered = filtered[filtered["brand"] == sel_brand]
    if sel_status != "All":
        _status_map = {"✅ Synced": "synced", "❌ Failed": "failed", "— Not pushed": None}
        _sv = _status_map[sel_status]
        if _sv is None:
            filtered = filtered[filtered["shopify_status"].isna() | (filtered["shopify_status"] == "")]
        else:
            filtered = filtered[filtered["shopify_status"] == _sv]

    st.caption(f"{len(filtered)} of {len(df_all)} records")

    if filtered.empty:
        st.info("No records match the current filters.")
        st.stop()

    filtered_records = filtered.to_dict(orient="records")

    # ── Shopify CSV preview (full format) ─────────────────────────────────────
    shopify_df = build_shopify_rows(filtered_records)

    sec("SHOPIFY CSV PREVIEW  ·  exactly as it will import into Shopify")
    st.caption(
        f"{len(shopify_df)} rows · {shopify_df['Handle'].nunique()} product(s) · "
        "Product-level fields appear only on first row of each product group — scroll right to see all columns."
    )
    st.dataframe(
        shopify_df,
        use_container_width=True,
        hide_index=True,
        height=500,
    )

    # ── Selection ─────────────────────────────────────────────────────────────
    def _status_label(s):
        if s == "synced":   return "✅ Synced"
        if s == "modified": return "◐ Modified"
        if s == "failed":   return "❌ Failed"
        return "— Not pushed"

    def _first_img(r):
        imgs = json.loads(r.get("images_json") or "[]") if isinstance(r.get("images_json"), str) else (r.get("images_json") or [])
        return imgs[0] if imgs else ""

    sec("SELECT  ·  tick rows to include in push")
    sel_rows = []
    for r in filtered_records:
        sel_rows.append({
            "_id":            r["id"],
            "Select":         False,
            "Shopify Status": _status_label(r.get("shopify_status")),
            "Product ID":     r.get("product_handle") or "",
            "SKU":            r.get("sku") or "",
            "Image":          _first_img(r),
            "Item Name":      r.get("item_name") or "",
            "Model":          r.get("model_name") or "",
            "Brand":          r.get("brand") or "",
            "Grade":          r.get("grade") or "",
            "Qty":            int(r.get("quantity") or 0),
            "Price (₹)":      float(r.get("selling_price") or 0),
            "OSP (₹)":        float(r.get("osp") or 0),
        })
    sel_edit_df = pd.DataFrame(sel_rows)
    edited_sel_df = st.data_editor(
        sel_edit_df,
        height=400,
        column_config={
            "_id":            st.column_config.NumberColumn("ID",             disabled=True),
            "Select":         st.column_config.CheckboxColumn("Select",        default=False),
            "Shopify Status": st.column_config.TextColumn("Shopify Status",   disabled=True, width="small"),
            "Product ID":     st.column_config.TextColumn("Product ID",       disabled=True, width="medium"),
            "SKU":            st.column_config.TextColumn("SKU",              disabled=True, width="medium"),
            "Image":          st.column_config.ImageColumn("Image",           width="small"),
            "Item Name":      st.column_config.TextColumn("Item Name",        disabled=True, width="large"),
            "Model":          st.column_config.TextColumn("Model",            disabled=True),
            "Brand":          st.column_config.TextColumn("Brand",            disabled=True),
            "Grade":          st.column_config.TextColumn("Grade",            disabled=True, width="small"),
            "Qty":            st.column_config.NumberColumn("Qty",            disabled=True, format="%d"),
            "Price (₹)":      st.column_config.NumberColumn("Price (₹)",     disabled=True, format="%.0f"),
            "OSP (₹)":        st.column_config.NumberColumn("OSP (₹)",       disabled=True, format="%.0f"),
        },
        column_order=["Select", "Shopify Status", "Product ID", "Image", "SKU", "Item Name", "Model", "Brand", "Grade", "Qty", "Price (₹)", "OSP (₹)"],
        hide_index=True,
        use_container_width=True,
        key="shopify_sel_editor",
    )

    selected_df = edited_sel_df[edited_sel_df["Select"] == True]
    n_selected = len(selected_df)
    st.caption(f"{n_selected} row(s) selected" if n_selected else "No rows selected — tick checkboxes to select.")

    id_to_row = {r["id"]: r for r in inv}

    def _build_push_rows(source_df):
        # productSet upserts the WHOLE product, so we must push every variant that
        # shares a selected row's product_handle — not just the ticked SKUs — or
        # Shopify would drop the omitted sibling variants.
        want_handles = set()
        loose_ids = set()
        for _, er in source_df.iterrows():
            r = id_to_row.get(int(er["_id"]))
            if not r:
                continue
            h = str(r.get("product_handle") or "").strip()
            if h:
                want_handles.add(h)
            else:
                loose_ids.add(r["id"])
        rows = []
        for r in inv:
            h = str(r.get("product_handle") or "").strip()
            if (h and h in want_handles) or (r["id"] in loose_ids):
                rows.append(dict(r))
        return rows

    sec("ACTIONS")
    act1, act2, act3 = st.columns([1.2, 1.5, 1.5])
    with act1:
        st.download_button(
            "⬇ Download Shopify CSV",
            shopify_df.to_csv(index=False).encode("utf-8"),
            "shopify_import.csv", "text/csv",
            use_container_width=True,
        )
    with act2:
        push_label = f"🚀 Push Selected ({n_selected})" if n_selected else "🚀 Push Selected"
        if st.button(push_label, type="primary", use_container_width=True, disabled=(n_selected == 0)):
            st.session_state["shopify_manage_push_rows"] = _build_push_rows(selected_df)
            rerun()
    with act3:
        if st.button("🚀 Push All Filtered", use_container_width=True):
            st.session_state["shopify_manage_push_rows"] = _build_push_rows(edited_sel_df)
            rerun()
