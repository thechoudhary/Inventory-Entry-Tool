"""End-to-end lifecycle test — runs against a throwaway temp DB and a mocked
Shopify client, so neither the real inventory.db nor Shopify are touched.

Run:  python tests/test_lifecycle_e2e.py
"""
import os, sys, json, tempfile, traceback

# Isolate the DB BEFORE importing db (DB_PATH is read at import time).
_tmpdir = tempfile.mkdtemp(prefix="inv_e2e_")
os.environ["INVENTORY_DB_PATH"] = os.path.join(_tmpdir, "inventory.db")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from sku import generate_sku, generate_product_handle, extract_physical_from_specs

PASS, FAIL = 0, 0
def check(label, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1; print(f"  PASS  {label}")
    else:
        FAIL += 1; print(f"  FAIL  {label}  {extra}")

ACTOR = {"id": 1, "username": "tester"}

def mid(kind, name):
    return next(r["id"] for r in db.list_master(kind, active_only=False) if r["name"] == name)

def build_draft(preview_variant, *, item, model, cat_id, brand_id, grade_id,
                grade_name, qty, specs=None, images=None, handle=None):
    v = dict(preview_variant)
    v["grade_id"]       = grade_id
    v["grade_name"]     = grade_name
    v["mrp"]            = 1000
    v["purchase_price"] = 500
    v["selling_price"]  = 900
    v["osp"]            = 1200
    v["lot_number"]     = "LOT1"
    return {
        "variants": [v],
        "item_name": item, "model_name": model,
        "product_handle": handle or v.get("product_handle"),
        "category_id": cat_id, "sub_category_id": None, "brand_id": brand_id,
        "capacity": 6, "capacity_unit": "kg", "source_url": None,
        "amazon_asin": "", "amazon_title": "", "amazon_price": "",
        "amazon_rating": "", "amazon_reviews": "", "amazon_availability": "",
        "description": "A washing machine", "amazon_about_this_item": "",
        "specifications": specs or {}, "images": images or [],
        "whats_in_the_box": "",
    }

def seed():
    db.init_db()
    db.add_master("categories", "Washing Machine", "WM", 0, ACTOR)
    db.add_master("brands", "Whirlpool", "WHP", 0, ACTOR)
    db.add_master("grades", "Grade A", "GA", 0, ACTOR)
    db.add_master("grades", "Grade B", "GB", 0, ACTOR)
    db.add_master("colours", "White", "WHT", 0, ACTOR)
    db.add_master("colours", "Black", "BLK", 0, ACTOR)

def row_by_sku(sku):
    return next((r for r in db.list_inventory() if r["sku"] == sku), None)


def test_db_lifecycle():
    print("\n[1] DB lifecycle (create / link variant / top-up / status)")
    seed()
    cat = mid("categories", "Washing Machine")
    brand = mid("brands", "Whirlpool")
    gA = mid("grades", "Grade A")
    gB = mid("grades", "Grade B")

    # --- Create product, variant 1 (Grade A, White)
    pv = db.preview_variants("Washer", "MagicClean", cat, None, brand, gA, ["White"], 3, ACTOR)[0]
    sku1, handle = pv["sku"], pv["product_handle"]
    check("handle is SKU prefix (cat-brand-model)", sku1.startswith(handle), f"{sku1} / {handle}")
    db.save_inventory_variants(build_draft(pv, item="Washer", model="MagicClean",
                               cat_id=cat, brand_id=brand, grade_id=gA, grade_name="Grade A", qty=3), ACTOR)
    r1 = row_by_sku(sku1)
    check("variant 1 created", r1 is not None)
    check("variant 1 qty = 3", r1 and r1["quantity"] == 3, r1 and r1["quantity"])
    check("variant 1 status not-synced", r1 and (r1["shopify_status"] in (None, "")))
    check("variant 1 synced_qty = 0", r1 and r1["shopify_synced_qty"] == 0)

    # --- Case 1: add a NEW variant (Grade B, Black) to the SAME product
    pv2 = db.preview_variants("Washer", "MagicClean", cat, None, brand, gB, ["Black"], 2, ACTOR)[0]
    sku2 = pv2["sku"]
    check("variant 2 new SKU differs", sku2 != sku1, f"{sku2}")
    check("variant 2 shares product handle", pv2["product_handle"] == handle, f"{pv2['product_handle']} vs {handle}")
    db.save_inventory_variants(build_draft(pv2, item="Washer", model="MagicClean",
                               cat_id=cat, brand_id=brand, grade_id=gB, grade_name="Grade B", qty=2), ACTOR)
    r2 = row_by_sku(sku2)
    check("variant 2 created linked to same handle", r2 and r2["product_handle"] == handle)
    handles = {r["product_handle"] for r in db.list_inventory()}
    check("both variants under ONE product handle", handles == {handle}, handles)

    # get_product_by_handle returns the shared product content (skips Amazon re-fetch)
    gp = db.get_product_by_handle(handle)
    check("get_product_by_handle finds existing product", gp is not None and gp["product_handle"] == handle)
    check("existing product carries shared content (description)", gp and gp.get("description") == "A washing machine", gp and gp.get("description"))
    check("get_product_by_handle(None) is None", db.get_product_by_handle(None) is None)
    check("get_product_by_handle(unknown) is None", db.get_product_by_handle("NO-SUCH-HANDLE") is None)

    # --- Simulate a successful push of BOTH variants (mark_synced with pushed qty)
    rows = {r["sku"]: r for r in db.list_inventory()}
    db.mark_synced({rows[sku1]["id"]: rows[sku1]["quantity"],
                    rows[sku2]["id"]: rows[sku2]["quantity"]})
    rows = {r["sku"]: r for r in db.list_inventory()}
    check("both variants synced", all(rows[s]["shopify_status"] == "synced" for s in (sku1, sku2)))
    check("synced_qty advanced to qty (v1=3)", rows[sku1]["shopify_synced_qty"] == 3, rows[sku1]["shopify_synced_qty"])
    check("synced_qty advanced to qty (v2=2)", rows[sku2]["shopify_synced_qty"] == 2, rows[sku2]["shopify_synced_qty"])

    # --- Case 2: top up qty on variant 1 ONLY (+4)
    pv_top = db.preview_variants("Washer", "MagicClean", cat, None, brand, gA, ["White"], 4, ACTOR)[0]
    check("top-up detects existing SKU", pv_top["existing_id"] == rows[sku1]["id"])
    check("top-up current qty = 3", pv_top["current_quantity"] == 3, pv_top["current_quantity"])
    db.save_inventory_variants(build_draft(pv_top, item="Washer", model="MagicClean",
                               cat_id=cat, brand_id=brand, grade_id=gA, grade_name="Grade A", qty=4), ACTOR)
    rows = {r["sku"]: r for r in db.list_inventory()}
    check("variant 1 qty incremented 3 -> 7", rows[sku1]["quantity"] == 7, rows[sku1]["quantity"])
    check("variant 1 flipped to MODIFIED", rows[sku1]["shopify_status"] == "modified", rows[sku1]["shopify_status"])
    check("variant 1 synced_qty still 3 (delta pending)", rows[sku1]["shopify_synced_qty"] == 3, rows[sku1]["shopify_synced_qty"])
    check("variant 2 UNTOUCHED (still synced)", rows[sku2]["shopify_status"] == "synced", rows[sku2]["shopify_status"])
    check("variant 2 qty unchanged = 2", rows[sku2]["quantity"] == 2)

    # --- Delta that WOULD be pushed for variant 1
    delta = rows[sku1]["quantity"] - rows[sku1]["shopify_synced_qty"]
    check("pending push delta for v1 = 4 (only the new units)", delta == 4, delta)

    # --- After re-push, synced_qty catches up, status synced
    db.mark_synced({rows[sku1]["id"]: 7})
    r1b = row_by_sku(sku1)
    check("after re-push synced_qty = 7", r1b["shopify_synced_qty"] == 7, r1b["shopify_synced_qty"])
    check("after re-push delta back to 0", r1b["quantity"] - r1b["shopify_synced_qty"] == 0)


def test_conflict_block():
    print("\n[2] Conflict handling (same SKU, different attributes is blocked)")
    # Reuse same DB/product; preview with same identity+colour+grade but it should match (not conflict).
    cat = mid("categories", "Washing Machine"); brand = mid("brands", "Whirlpool")
    gA = mid("grades", "Grade A")
    pv = db.preview_variants("Washer", "MagicClean", cat, None, brand, gA, ["White"], 1, ACTOR)[0]
    check("re-preview existing variant is NOT a conflict", pv["conflict"] is False, pv["action"])


def test_content_edit_and_physical():
    print("\n[3] Product-content edit re-derives weight/dimensions from specs")
    cat = mid("categories", "Washing Machine"); brand = mid("brands", "Whirlpool")
    gA = mid("grades", "Grade A")
    pv = db.preview_variants("Dryer", "TurboDry", cat, None, brand, gA, ["White"], 1, ACTOR)[0]
    db.save_inventory_variants(build_draft(pv, item="Dryer", model="TurboDry",
                               cat_id=cat, brand_id=brand, grade_id=gA, grade_name="Grade A", qty=1), ACTOR)
    r = row_by_sku(pv["sku"])
    db.update_product_content([r["id"]], {
        "specifications": {"Product Dimensions": "58D x 55W x 85H Centimeters",
                            "Item Weight": "12.5 kg"},
    }, ACTOR)
    r2 = db.get_inventory_by_id(r["id"])
    check("packed_length derived = 58", r2["packed_length"] == 58.0, r2["packed_length"])
    check("packed_width derived = 55", r2["packed_width"] == 55.0, r2["packed_width"])
    check("packed_height derived = 85", r2["packed_height"] == 85.0, r2["packed_height"])
    check("weight derived = 12.5", r2["weight_grams"] == 12.5, r2["weight_grams"])
    check("weight unit derived = kg", r2["weight_unit"] == "kg", r2["weight_unit"])


def test_shopify_delta_decision():
    print("\n[4] Shopify push: SET for new variant, ADJUST(delta) for existing + options")
    from shopify_integration import ShopifyClient
    c = ShopifyClient("teststore", access_token="x")
    calls = {"set": [], "adjust": []}
    captured = {}
    c.get_default_location_id = lambda: "gid://loc/1"
    c.add_images = lambda pid, imgs, alt=None: {"added": 0, "skipped": 0}
    existing = {"SKU-EXIST": {"id": "gid://v/9", "product": {"id": "gid://p/1"}}}
    c.find_variant_by_sku = lambda sku: existing.get(sku)
    def fake_graphql(query, variables=None, retries=3):
        captured["input"] = variables["input"]
        nodes = []
        for i, v in enumerate(variables["input"]["variants"]):
            sku = v["inventoryItem"]["sku"]
            nodes.append({"id": f"gid://v/{i}", "sku": sku,
                          "inventoryItem": {"id": f"gid://inv/{sku}"}})
        return {"productSet": {"product": {"id": "gid://p/1",
                "variants": {"nodes": nodes}}, "userErrors": []}}
    c.graphql = fake_graphql
    c.set_inventory = lambda inv_id, qty: calls["set"].append((inv_id, qty))
    c.adjust_inventory = lambda inv_id, delta: calls["adjust"].append((inv_id, delta))

    items = [
        {"sku": "SKU-NEW",   "grade": "Grade A", "quantity": 3, "inventory_delta": 3,
         "selling_price": 900, "images_json": "[]", "colours_json": "[\"White\"]"},
        {"sku": "SKU-EXIST", "grade": "Grade B", "quantity": 5, "inventory_delta": 3,
         "selling_price": 900, "images_json": "[]", "colours_json": "[\"Black\"]"},
    ]
    res = c.upsert_product_group(items)
    check("NEW variant used SET with full qty 3", calls["set"] == [("gid://inv/SKU-NEW", 3)], calls["set"])
    check("EXISTING variant used ADJUST with delta +3", calls["adjust"] == [("gid://inv/SKU-EXIST", 3)], calls["adjust"])
    check("inventory_ok lists both applied SKUs", set(res.get("inventory_ok", [])) == {"SKU-NEW", "SKU-EXIST"}, res.get("inventory_ok"))

    # Same-grade, different-colour variants must get a Color option (no collision)
    calls2 = {"set": []}
    captured.clear()
    c2 = ShopifyClient("teststore", access_token="x")
    c2.get_default_location_id = lambda: "gid://loc/1"
    c2.add_images = lambda pid, imgs, alt=None: {"added": 0, "skipped": 0}
    c2.find_variant_by_sku = lambda sku: None
    def fake_graphql2(query, variables=None, retries=3):
        captured["input"] = variables["input"]
        nodes = [{"id": f"gid://v/{i}", "sku": v["inventoryItem"]["sku"],
                  "inventoryItem": {"id": f"gid://inv/{i}"}}
                 for i, v in enumerate(variables["input"]["variants"])]
        return {"productSet": {"product": {"id": "gid://p/2", "variants": {"nodes": nodes}}, "userErrors": []}}
    c2.graphql = fake_graphql2
    c2.set_inventory = lambda inv_id, qty: calls2["set"].append((inv_id, qty))
    same_grade = [
        {"sku": "A-RED",  "grade": "Grade A", "quantity": 1, "selling_price": 1, "images_json": "[]", "colours_json": "[\"Red\"]"},
        {"sku": "A-BLUE", "grade": "Grade A", "quantity": 1, "selling_price": 1, "images_json": "[]", "colours_json": "[\"Blue\"]"},
    ]
    c2.upsert_product_group(same_grade)
    opt_names = [o["name"] for o in captured["input"]["productOptions"]]
    check("same-grade variants get BOTH Grade + Color options", opt_names == ["Grade", "Color"], opt_names)
    combos = [tuple(ov["name"] for ov in v["optionValues"]) for v in captured["input"]["variants"]]
    check("variant option combos are unique", len(combos) == len(set(combos)), combos)


def test_stage_upload_and_add_images():
    print("\n[5] Local image staged-upload + alt dedup; remote URLs pass through")
    from shopify_integration import ShopifyClient
    c = ShopifyClient("teststore", access_token="x")

    # add_images uses alt_overrides so a re-staged local image dedups by stable alt
    media_calls = []
    def fake_graphql(query, variables=None, retries=3):
        if "ProductMedia" in query:   # existing-media query
            return {"product": {"media": {"nodes": [{"alt": "img_local_abc123"}],
                                          "pageInfo": {"hasNextPage": False, "endCursor": None}}}}
        if "AddImages" in query:      # productCreateMedia
            media_calls.append(variables["media"])
            return {"productCreateMedia": {"media": variables["media"], "mediaUserErrors": []}}
        return {}
    c.graphql = fake_graphql
    res = c.add_images("gid://p/1",
                       ["https://m.media-amazon.com/img1.jpg",
                        "https://shopify-staged/upload/xyz"],
                       alt_overrides={"https://shopify-staged/upload/xyz": "img_local_abc123"})
    # the staged image's stable alt already exists → only the amazon one is added
    added_alts = [m["alt"] for m in media_calls[0]] if media_calls else []
    check("remote (amazon) image added", any("amazon" not in a for a in added_alts) and len(added_alts) == 1, added_alts)
    check("re-staged local image deduped by stable alt", "img_local_abc123" not in added_alts, added_alts)

    # stage_upload: posts bytes to the staged target and returns the resourceUrl
    posted = {}
    def fake_graphql2(query, variables=None, retries=3):
        return {"stagedUploadsCreate": {
            "stagedTargets": [{"url": "https://bucket.example/upload",
                               "resourceUrl": "https://shopify-staged/resource/xyz",
                               "parameters": [{"name": "key", "value": "k1"}]}],
            "userErrors": []}}
    c.graphql = fake_graphql2
    import shopify_integration as si
    _orig_post = si.requests.post
    def fake_post(url, data=None, files=None, timeout=None):
        posted["url"] = url; posted["has_file"] = files is not None
        class _R: status_code = 201; text = ""
        return _R()
    si.requests.post = fake_post
    try:
        rurl = c.stage_upload(b"\xff\xd8\xff", "abc123.jpg", "image/jpeg")
    finally:
        si.requests.post = _orig_post
    check("stage_upload returns the resourceUrl", rurl == "https://shopify-staged/resource/xyz", rurl)
    check("stage_upload POSTed the file bytes to bucket", posted.get("has_file") and "bucket" in posted.get("url", ""), posted)


def test_title_case_and_variant_image():
    print("\n[6] Title-case product title + per-variant image assignment")
    from shopify_integration import ShopifyClient
    c = ShopifyClient("teststore", access_token="x")
    check("title-case capitalises each word, preserves rest",
          c._title_case("whirlpool Washing machine 8kg") == "Whirlpool Washing Machine 8kg",
          c._title_case("whirlpool Washing machine 8kg"))

    appended = {}
    c.get_default_location_id = lambda: "gid://loc/1"
    c.find_variant_by_sku = lambda sku: None
    c.set_inventory = lambda inv_id, qty: None
    def fake_graphql(query, variables=None, retries=3):
        if "productSet" in query:
            return {"productSet": {"product": {"id": "gid://p/1", "variants": {"nodes": [
                {"id": "gid://var/1", "sku": "SKU-V1", "inventoryItem": {"id": "gid://inv/1"}}]}},
                "userErrors": []}}
        if "ProductMedia" in query:
            return {"product": {"media": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}
        if "AddImages" in query:
            media = [{"id": "gid://m/1", "alt": m["alt"]} for m in variables["media"]]
            return {"productCreateMedia": {"media": media, "mediaUserErrors": []}}
        if "AppendVariantMedia" in query:
            appended["variantMedia"] = variables["variantMedia"]
            return {"productVariantAppendMedia": {"userErrors": []}}
        if "stagedTitle" in query:
            return {}
        return {}
    c.graphql = fake_graphql
    items = [{"sku": "SKU-V1", "grade": "Grade A", "quantity": 1, "selling_price": 1,
              "images_json": "[]", "colours_json": "[]",
              "variant_image": "https://staged/vimg", "item_name": "whirlpool washer"}]
    c.upsert_product_group(items, alt_overrides={"https://staged/vimg": "img_local_v1"})
    vm = appended.get("variantMedia", [])
    check("variant image assigned to its variant", vm == [{"variantId": "gid://var/1", "mediaIds": ["gid://m/1"]}], vm)


def test_field_mappings():
    print("\n[7] Field mappings: tags, taxable, cost, HS code, warehouse, model")
    from shopify_integration import ShopifyClient
    from sku import extract_model_from_specs
    c = ShopifyClient("teststore", access_token="x")

    # #6 Model from specs, year stripped
    check("model from specs (no year)",
          extract_model_from_specs({"Model": "WX-1234 2023"}, "fallback") == "WX-1234",
          extract_model_from_specs({"Model": "WX-1234 2023"}, "fallback"))
    check("model falls back when no spec", extract_model_from_specs({}, "FallbackModel 2021") == "FallbackModel",
          extract_model_from_specs({}, "FallbackModel 2021"))
    check("model scanned from about-text 'Model/Year' (year stripped)",
          extract_model_from_specs({}, "model_number", extra_text="Model/Year : VA Q 60 5S/2024") == "VA Q 60 5S",
          extract_model_from_specs({}, "model_number", extra_text="Model/Year : VA Q 60 5S/2024"))

    # #2/#3/#4 tags — all grades + Title-cased brand + category + sub-category
    base = {"item_name": "whirlpool washer", "brand": "WHIRLPOOL", "category": "Large",
            "sub_category": "Automatic", "grade": "Grade A", "product_handle": "AMT-WHP-WHIWASMAC",
            "model_name": "MAGIC CLEAN 6.0 2024",
            "specifications_json": '{"Model": "WX-9"}'}
    pin = c._build_product_input(base, all_grades=["Grade A", "Grade B", "Fresh"])
    tags = pin["tags"]
    check("title is Title Case", pin["title"] == "Whirlpool Washer", pin["title"])
    check("handle = our product_handle (lowercased)", pin.get("handle") == "amt-whp-whiwasmac", pin.get("handle"))
    check("brand tag Title-cased (Whirlpool)", "Whirlpool" in tags and "WHIRLPOOL" not in tags, tags)
    check("tags include category + sub-category", "Large" in tags and "Automatic" in tags, tags)
    check("tags include ALL grades", all(g in tags for g in ("Grade A", "Grade B", "Fresh")), tags)
    _mf = {m["key"]: m["value"] for m in pin.get("metafields", [])}
    check("model metafield = Model Name field (year stripped)", _mf.get("model") == "MAGIC CLEAN 6.0", _mf.get("model"))

    # #7/#8/#9/#10 variant input
    vi = c._variant_input_for_set({"sku": "S1", "grade": "Grade A", "selling_price": 900,
                                   "purchase_price": 500, "hs_code": "845012",
                                   "warehouse_location": "BS01-WH01"}, has_colour=False)
    check("#7 taxable is False", vi.get("taxable") is False, vi.get("taxable"))
    check("#8 cost = purchase price", vi["inventoryItem"].get("cost") == "500", vi["inventoryItem"].get("cost"))
    check("#9 HS code passed", vi["inventoryItem"].get("harmonizedSystemCode") == "845012", vi["inventoryItem"].get("harmonizedSystemCode"))
    # Invalid HS (too short) must be skipped, not sent — so it can't fail the atomic push
    vi_bad = c._variant_input_for_set({"sku": "S2", "grade": "Grade A", "selling_price": 1,
                                       "hs_code": "123"}, has_colour=False)
    check("invalid HS code is skipped (not sent)", "harmonizedSystemCode" not in vi_bad["inventoryItem"], vi_bad["inventoryItem"])
    check("#10 warehouse NOT in productSet input (set separately after)", "metafields" not in vi, vi.get("metafields"))

    # Shopify Standard Category — resolve leaf → taxonomy gid, exact-leaf preferred
    cc = ShopifyClient("teststore", access_token="x")
    def fake_tax(query, variables=None, retries=3):
        return {"taxonomy": {"categories": {"nodes": [
            {"id": "gid://shopify/TaxonomyCategory/hg-9-8-6", "name": "Washing Machines",
             "fullName": "Home & Garden > ... > Washing Machines", "isLeaf": True},
            {"id": "gid://shopify/TaxonomyCategory/hg-10", "name": "Washing Machine Cleaners",
             "fullName": "... > Washing Machine Cleaners", "isLeaf": True},
        ]}}}
    cc.graphql = fake_tax
    gid = cc.resolve_category_id("Washing Machines")
    check("category resolves to exact-leaf gid (not the cleaner)",
          gid == "gid://shopify/TaxonomyCategory/hg-9-8-6", gid)
    check("category resolution is cached", "washing machines" in cc._category_cache)
    check("blank category leaf = None", cc.resolve_category_id("") is None)
    # and it lands on the product input
    base2 = dict(base); base2["shopify_category"] = "Washing Machines"
    cc.find_variant_by_sku = lambda s: None
    pin2 = cc._build_product_input(base2, all_grades=["Grade A"])
    check("product input carries category gid", pin2.get("category") == "gid://shopify/TaxonomyCategory/hg-9-8-6", pin2.get("category"))

    # stored taxonomy gid (from the dropdown) is used directly, no live resolve
    base3 = dict(base); base3["shopify_category_id"] = "gid://shopify/TaxonomyCategory/hg-9-8-6"
    base3.pop("shopify_category", None)
    cc3 = ShopifyClient("teststore", access_token="x")
    cc3.graphql = lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not resolve when gid present"))
    cc3.find_variant_by_sku = lambda s: None
    pin3 = cc3._build_product_input(base3, all_grades=["Grade A"])
    check("stored gid used directly (no API resolve)", pin3.get("category") == "gid://shopify/TaxonomyCategory/hg-9-8-6", pin3.get("category"))


if __name__ == "__main__":
    try:
        test_db_lifecycle()
        test_conflict_block()
        test_content_edit_and_physical()
        test_shopify_delta_decision()
        test_stage_upload_and_add_images()
        test_title_case_and_variant_image()
        test_field_mappings()
    except Exception:
        traceback.print_exc(); FAIL += 1
    print(f"\n{'='*50}\nRESULT: {PASS} passed, {FAIL} failed\n{'='*50}")
    sys.exit(1 if FAIL else 0)
