import time
import json
import re
import uuid
import hashlib
import requests

SHOPIFY_API_VERSION = "2026-10"


class ShopifyAPIError(RuntimeError):
    pass


def generate_access_token(store: str, client_id: str, client_secret: str) -> str:
    store = store.replace("https://", "").replace("http://", "").strip("/")
    if store.endswith(".myshopify.com"):
        store = store[: -len(".myshopify.com")]
    resp = requests.post(
        f"https://{store}.myshopify.com/admin/oauth/access_token",
        headers={"Content-Type": "application/json"},
        json={"grant_type": "client_credentials", "client_id": client_id, "client_secret": client_secret},
        timeout=30,
    )
    if not resp.ok:
        raise ShopifyAPIError(f"Token generation failed ({resp.status_code}): {resp.text}")
    token = resp.json().get("access_token")
    if not token:
        raise ShopifyAPIError(f"No access_token in response: {resp.json()}")
    return token


def _parse_json_list(val):
    try:
        result = json.loads(val or "[]")
        return result if isinstance(result, list) else []
    except Exception:
        return []


def _clean(v):
    """Coerce a value to a trimmed string, treating None and pandas NaN as empty
    (so a NaN from a DataFrame row never becomes the literal string 'nan')."""
    if v is None:
        return ""
    if isinstance(v, float) and v != v:   # NaN
        return ""
    return str(v).strip()


class ShopifyClient:
    def __init__(self, store: str, access_token: str = "", client_id: str = "", client_secret: str = ""):
        store = store.replace("https://", "").replace("http://", "").strip("/")
        if not store.endswith(".myshopify.com"):
            store += ".myshopify.com"
        self.store        = store
        self._client_id     = client_id
        self._client_secret = client_secret
        if not access_token and client_id and client_secret:
            access_token = generate_access_token(store, client_id, client_secret)
        if not access_token:
            raise ShopifyAPIError("Either access_token or client_id+client_secret must be provided.")
        self._access_token = access_token
        self.graphql_url   = f"https://{store}/admin/api/{SHOPIFY_API_VERSION}/graphql.json"
        self.session       = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "X-Shopify-Access-Token": access_token,
        })
        self._location_id = None
        self._category_cache = {}   # leaf name (casefold) → taxonomy category gid
        self._wh_choices_cache = None   # valid warehouse metafield choices

    # ── Core GraphQL ──────────────────────────────────────────────────────────

    def _set_token(self, token: str):
        self._access_token = token
        self.session.headers.update({"X-Shopify-Access-Token": token})

    def graphql(self, query: str, variables: dict = None, retries: int = 3):
        for attempt in range(retries):
            try:
                resp = self.session.post(
                    self.graphql_url,
                    json={"query": query, "variables": variables or {}},
                    timeout=60,
                )
                if resp.status_code == 401 and self._client_id and self._client_secret and attempt == 0:
                    self._set_token(generate_access_token(self.store, self._client_id, self._client_secret))
                    continue
                if resp.status_code == 429 or resp.status_code >= 500:
                    if attempt < retries - 1:
                        time.sleep(int(resp.headers.get("Retry-After", 2 ** attempt)))
                        continue
                resp.raise_for_status()
                body = resp.json()
                if body.get("errors"):
                    raise ShopifyAPIError(str(body["errors"]))
                return body["data"]
            except requests.RequestException as exc:
                if attempt == retries - 1:
                    raise ShopifyAPIError(f"HTTP request failed: {exc}") from exc
                time.sleep(2 ** attempt)
        raise ShopifyAPIError("Shopify request failed after retries.")

    # ── Lookups ───────────────────────────────────────────────────────────────

    def find_variant_by_sku(self, sku: str):
        query = """
        query FindVariant($query: String!) {
          productVariants(first: 100, query: $query) {
            nodes {
              id sku
              inventoryItem { id }
              product { id title handle }
            }
          }
        }
        """
        safe = sku.replace("\\", "\\\\").replace('"', '\\"')
        data = self.graphql(query, {"query": f'sku:"{safe}"'})
        matches = [v for v in data["productVariants"]["nodes"] if v.get("sku") == sku]
        if len(matches) > 1:
            raise ShopifyAPIError(f"SKU {sku!r} is duplicated in Shopify — resolve manually.")
        return matches[0] if matches else None

    def fetch_all_taxonomy(self, leaves_only: bool = True):
        """Paginate Shopify's full Standard Product Taxonomy and return nodes as
        [{gid, name, full_name}]. Leaves only by default (what we offer to pick)."""
        out = []
        cursor = None
        q = """query($after: String) {
          taxonomy { categories(first: 250, after: $after) {
            nodes { id name fullName isLeaf }
            pageInfo { hasNextPage endCursor }
          } }
        }"""
        while True:
            conn = self.graphql(q, {"after": cursor})["taxonomy"]["categories"]
            for n in conn["nodes"]:
                if (not leaves_only) or n.get("isLeaf"):
                    out.append({"gid": n["id"], "name": n.get("name", ""),
                                "full_name": n.get("fullName", "")})
            if not conn["pageInfo"]["hasNextPage"]:
                break
            cursor = conn["pageInfo"]["endCursor"]
        return out

    def warehouse_choices(self):
        """Valid choices for the custom.warehouse_location variant metafield, so we
        never send a value Shopify will reject. Empty set if no definition/choices."""
        if self._wh_choices_cache is not None:
            return self._wh_choices_cache
        choices = set()
        try:
            q = """{ metafieldDefinitions(first: 50, ownerType: PRODUCTVARIANT) {
                     nodes { namespace key validations { name value } } } }"""
            for n in self.graphql(q)["metafieldDefinitions"]["nodes"]:
                if n["namespace"] == "custom" and n["key"] == "warehouse_location":
                    for v in n["validations"]:
                        if v["name"] == "choices" and v.get("value"):
                            try:
                                choices = set(json.loads(v["value"]))
                            except Exception:
                                pass
        except Exception:
            pass
        self._wh_choices_cache = choices
        return choices

    def set_metafields(self, metafields: list):
        """Set metafields outside productSet (e.g. the warehouse choices metafield),
        so a blank/invalid one can't block the atomic product push.
        metafields: [{ownerId, namespace, key, type, value}]."""
        if not metafields:
            return
        mutation = """
        mutation SetMetafields($mf: [MetafieldsSetInput!]!) {
          metafieldsSet(metafields: $mf) {
            userErrors { field message }
          }
        }
        """
        res = self.graphql(mutation, {"mf": metafields})["metafieldsSet"]
        if res["userErrors"]:
            raise ShopifyAPIError(f"metafieldsSet: {res['userErrors']}")

    def resolve_category_id(self, leaf_name: str):
        """Resolve a Shopify Standard Taxonomy leaf name (e.g. 'Washing Machines')
        to its category gid. Prefers an exact leaf-name match so 'Washing Machines'
        doesn't grab 'Washing Machine Cleaners'. Cached per client."""
        leaf = str(leaf_name or "").strip()
        if not leaf:
            return None
        key = leaf.casefold()
        if key in self._category_cache:
            return self._category_cache[key]
        q = """query($s: String!) {
          taxonomy { categories(first: 10, search: $s) {
            nodes { id name fullName isLeaf }
          } }
        }"""
        gid = None
        try:
            nodes = self.graphql(q, {"s": leaf})["taxonomy"]["categories"]["nodes"]
            exact = [n for n in nodes if n.get("isLeaf") and str(n.get("name", "")).casefold() == key]
            if exact:
                gid = exact[0]["id"]
            elif nodes:
                leafs = [n for n in nodes if n.get("isLeaf")]
                gid = (leafs[0] if leafs else nodes[0])["id"]
        except Exception:
            gid = None
        self._category_cache[key] = gid
        return gid

    def get_default_location_id(self) -> str:
        if self._location_id:
            return self._location_id
        try:
            data  = self.graphql("query { locations(first: 1) { nodes { id name } } }")
            nodes = data["locations"]["nodes"]
            if not nodes:
                raise ShopifyAPIError("No locations found.")
            self._location_id = nodes[0]["id"]
            return self._location_id
        except ShopifyAPIError as e:
            if "ACCESS_DENIED" in str(e) or "Access denied" in str(e):
                raise PermissionError("read_locations scope not granted") from e
            raise

    # ── Images ────────────────────────────────────────────────────────────────

    def stage_upload(self, file_bytes: bytes, filename: str, mime_type: str = "image/jpeg") -> str:
        """Upload raw image bytes to Shopify's staging bucket and return a
        resourceUrl that productCreateMedia can ingest. Used for locally-uploaded
        images whose own URL (e.g. localhost) Shopify cannot fetch."""
        q = """
        mutation stagedUploadsCreate($input: [StagedUploadInput!]!) {
          stagedUploadsCreate(input: $input) {
            stagedTargets { url resourceUrl parameters { name value } }
            userErrors { field message }
          }
        }
        """
        data = self.graphql(q, {"input": [{
            "resource": "IMAGE", "filename": filename,
            "mimeType": mime_type, "httpMethod": "POST",
        }]})["stagedUploadsCreate"]
        if data["userErrors"]:
            raise ShopifyAPIError(f"stagedUploadsCreate: {data['userErrors']}")
        if not data["stagedTargets"]:
            raise ShopifyAPIError("stagedUploadsCreate returned no targets")
        target = data["stagedTargets"][0]
        # POST the bytes to the staging bucket (NOT the Shopify API host).
        form = [(p["name"], p["value"]) for p in target["parameters"]]
        resp = requests.post(target["url"], data=form,
                             files={"file": (filename, file_bytes, mime_type)}, timeout=60)
        if resp.status_code not in (200, 201, 204):
            raise ShopifyAPIError(f"staged upload POST failed ({resp.status_code}): {resp.text[:200]}")
        return target["resourceUrl"]

    def add_images(self, product_id: str, image_urls: list, alt_overrides: dict = None) -> dict:
        alt_overrides = alt_overrides or {}
        image_urls = list(dict.fromkeys(
            url.strip() for url in image_urls if isinstance(url, str) and url.strip()
        ))
        if not image_urls:
            return {"added": 0, "skipped": 0}

        q = """
        query ProductMedia($id: ID!, $after: String) {
          product(id: $id) {
            media(first: 100, after: $after) {
              nodes { ... on MediaImage { id alt } }
              pageInfo { hasNextPage endCursor }
            }
          }
        }
        """
        existing = set()
        media_by_alt = {}   # alt → media id (existing + newly created)
        after = None
        while True:
            result  = self.graphql(q, {"id": product_id, "after": after})
            product = result.get("product")
            if not product:
                raise ShopifyAPIError(f"Product not found: {product_id}")
            for m in product["media"]["nodes"]:
                if m.get("alt"):
                    existing.add(m["alt"])
                    if m.get("id"):
                        media_by_alt[m["alt"]] = m["id"]
            if not product["media"]["pageInfo"]["hasNextPage"]:
                break
            after = product["media"]["pageInfo"]["endCursor"]

        to_add = []
        for url in image_urls:
            # A caller can pin a stable alt (e.g. a content hash) so re-pushing a
            # locally-uploaded image that was re-staged to a new URL still dedups.
            k = self._img_alt(url, alt_overrides)
            if k not in existing:
                to_add.append({"originalSource": url, "alt": k, "mediaContentType": "IMAGE"})
                existing.add(k)

        if not to_add:
            return {"added": 0, "skipped": len(image_urls), "media_by_alt": media_by_alt}

        mutation = """
        mutation AddImages($productId: ID!, $media: [CreateMediaInput!]!) {
          productCreateMedia(productId: $productId, media: $media) {
            media { ... on MediaImage { id alt } }
            mediaUserErrors { field message }
          }
        }
        """
        result = self.graphql(mutation, {"productId": product_id, "media": to_add})["productCreateMedia"]
        if result["mediaUserErrors"]:
            raise ShopifyAPIError(f"Image upload: {result['mediaUserErrors']}")
        for m in result["media"]:
            if m.get("alt") and m.get("id"):
                media_by_alt[m["alt"]] = m["id"]
        return {"added": len(result["media"]), "skipped": len(image_urls) - len(to_add),
                "media_by_alt": media_by_alt}

    @staticmethod
    def _img_alt(url, alt_overrides=None):
        """Stable media alt for an image URL (lets re-pushes dedup / match)."""
        alt_overrides = alt_overrides or {}
        return alt_overrides.get(url) or ("img_" + hashlib.sha256(str(url).strip().encode()).hexdigest())

    def _wait_media_ready(self, media_ids: list, timeout: int = 25):
        """Poll until the given media are READY (freshly-uploaded images process
        asynchronously; variants can't be attached to non-ready media)."""
        ids = list({m for m in media_ids if m})
        if not ids:
            return
        q = """query($ids: [ID!]!) { nodes(ids: $ids) { ... on MediaImage { id status } } }"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            nodes = [n for n in self.graphql(q, {"ids": ids})["nodes"] if n]
            statuses = [n.get("status") for n in nodes]
            if statuses and all(s == "READY" for s in statuses):
                return
            if any(s == "FAILED" for s in statuses):
                return
            time.sleep(2)

    def append_variant_media(self, product_id: str, variant_media: list):
        """Attach already-uploaded product media to specific variants.
        variant_media: [{"variantId": ..., "mediaIds": [...]}]."""
        if not variant_media:
            return
        mutation = """
        mutation AppendVariantMedia($productId: ID!, $variantMedia: [ProductVariantAppendMediaInput!]!) {
          productVariantAppendMedia(productId: $productId, variantMedia: $variantMedia) {
            userErrors { field message }
          }
        }
        """
        result = self.graphql(mutation, {"productId": product_id, "variantMedia": variant_media})["productVariantAppendMedia"]
        # "already has attached media" just means the image is already on the variant
        # from a prior push — harmless, so ignore those and only raise on real errors.
        errs = [e for e in result["userErrors"]
                if "already has attached media" not in str(e.get("message", "")).lower()]
        if errs:
            raise ShopifyAPIError(f"append variant media: {errs}")

    # ── Inventory ─────────────────────────────────────────────────────────────

    def _available_at(self, inventory_item_id: str, location_id: str) -> int:
        """Current 'available' quantity for an inventory item at a location (0 if not stocked).
        The 2026-10 API requires this as changeFromQuantity for safe set/adjust."""
        q = """query($id: ID!, $loc: ID!) {
          inventoryItem(id: $id) { inventoryLevel(locationId: $loc) {
            quantities(names: ["available"]) { quantity }
          } }
        }"""
        try:
            lvl = self.graphql(q, {"id": inventory_item_id, "loc": location_id})["inventoryItem"]["inventoryLevel"]
            if lvl and lvl.get("quantities"):
                return int(lvl["quantities"][0]["quantity"])
        except Exception:
            pass
        return 0

    def set_inventory(self, inventory_item_id: str, quantity: int):
        location_id = self.get_default_location_id()
        current = self._available_at(inventory_item_id, location_id)
        mutation = """
        mutation SetInventory($input: InventorySetQuantitiesInput!, $idem: String!) {
          inventorySetQuantities(input: $input) @idempotent(key: $idem) {
            inventoryAdjustmentGroup { reason }
            userErrors { field message }
          }
        }
        """
        result = self.graphql(mutation, {
            "idem": uuid.uuid4().hex,
            "input": {
                "name": "available",
                "reason": "correction",
                "quantities": [{"inventoryItemId": inventory_item_id,
                                "locationId": location_id, "quantity": int(quantity),
                                "changeFromQuantity": current}],
            }
        })["inventorySetQuantities"]
        if result["userErrors"]:
            raise ShopifyAPIError(f"Set inventory: {result['userErrors']}")

    def _try_set_inventory(self, inventory_item_id: str, quantity: int) -> str:
        try:
            self.set_inventory(inventory_item_id, quantity)
            return ""
        except PermissionError:
            return "skipped (read_locations/write_inventory scope not granted)"
        except ShopifyAPIError as e:
            return f"failed: {e}"

    def adjust_inventory(self, inventory_item_id: str, delta: int):
        """Adjust available inventory by a signed delta (does NOT replace the
        count), so quantities sold on Shopify between pushes are preserved."""
        location_id = self.get_default_location_id()
        current = self._available_at(inventory_item_id, location_id)
        mutation = """
        mutation AdjustInventory($input: InventoryAdjustQuantitiesInput!, $idem: String!) {
          inventoryAdjustQuantities(input: $input) @idempotent(key: $idem) {
            inventoryAdjustmentGroup { reason }
            userErrors { field message }
          }
        }
        """
        result = self.graphql(mutation, {
            "idem": uuid.uuid4().hex,
            "input": {
                "name": "available",
                "reason": "received",
                "changes": [{"inventoryItemId": inventory_item_id,
                             "locationId": location_id, "delta": int(delta),
                             "changeFromQuantity": current}],
            }
        })["inventoryAdjustQuantities"]
        if result["userErrors"]:
            raise ShopifyAPIError(f"Adjust inventory: {result['userErrors']}")

    def _try_adjust_inventory(self, inventory_item_id: str, delta: int) -> str:
        try:
            self.adjust_inventory(inventory_item_id, delta)
            return ""
        except PermissionError:
            return "skipped (read_locations/write_inventory scope not granted)"
        except ShopifyAPIError as e:
            return f"failed: {e}"

    # ── Shared helpers ────────────────────────────────────────────────────────

    @staticmethod
    def _title_case(s: str) -> str:
        """Capitalise the first letter of each word, preserving the rest (so
        'whirlpool Washing machine' → 'Whirlpool Washing Machine', '8kg' stays '8kg')."""
        return " ".join((w[:1].upper() + w[1:]) if w else w for w in str(s or "").split(" "))

    @staticmethod
    def _brand_case(s: str) -> str:
        """Brand tag: first letter of each word uppercase, the rest lowercase
        ('WHIRLPOOL' → 'Whirlpool', 'lg electronics' → 'Lg Electronics')."""
        return " ".join(w.capitalize() for w in str(s or "").split())

    def _build_product_input(self, base: dict, all_grades=None) -> dict:
        """Build the product-level fields dict from a DB row.
        all_grades: every grade across the product's variants (for tags)."""
        title = self._title_case(str(base.get("item_name") or "").strip())

        # Tags: store + brand (Title case) + category + sub-category + every grade.
        tag_parts = ["Bettr Stores"]
        _brand = self._brand_case(base.get("brand"))
        if _brand:
            tag_parts.append(_brand)
        for f in ("category", "sub_category"):
            v = str(base.get(f) or "").strip()
            if v:
                tag_parts.append(v)
        _grades = list(all_grades) if all_grades else [str(base.get("grade") or "").strip()]
        for g in _grades:
            g = str(g or "").strip()
            if g and g not in tag_parts:
                tag_parts.append(g)

        specs_dict = {}
        specs_str = ""
        try:
            specs_dict = json.loads(base.get("specifications_json") or "{}")
            specs_str  = "\n".join(f"{k}: {v}" for k, v in specs_dict.items()) if specs_dict else ""
        except Exception:
            pass

        metafields = []
        if base.get("amazon_about_this_item"):
            metafields.append({"namespace": "custom", "key": "key_features",
                                "value": str(base["amazon_about_this_item"]), "type": "multi_line_text_field"})
        # Model metafield = the Model Name field (year stripped), not scraped text.
        _model = re.sub(r"\b(?:19|20)\d{2}\b", "", _clean(base.get("model_name")))
        _model = re.sub(r"\s{2,}", " ", _model).strip(" -,/")
        if _model:
            metafields.append({"namespace": "custom", "key": "model",
                                "value": _model, "type": "single_line_text_field"})
        if base.get("osp"):
            metafields.append({"namespace": "custom", "key": "osp",
                                "value": str(base["osp"]), "type": "number_decimal"})
        if specs_str:
            metafields.append({"namespace": "custom", "key": "specifications",
                                "value": specs_str, "type": "multi_line_text_field"})
        if base.get("whats_in_the_box"):
            metafields.append({"namespace": "custom", "key": "whats_in_the_box",
                                "value": str(base["whats_in_the_box"]), "type": "multi_line_text_field"})

        inp = {
            "title": title,
            "descriptionHtml": str(base.get("description") or ""),
            "vendor": str(base.get("brand") or ""),
            "productType": str(base.get("sub_category") or ""),
            "status": "ACTIVE",
            "tags": ", ".join(tag_parts),
            "seo": {
                "title": str(base.get("amazon_title") or title),
                "description": str(base.get("description") or "")[:320],
            },
        }
        # Use OUR product handle as the Shopify handle (stable product id) instead
        # of letting Shopify auto-generate one from the title.
        _handle = str(base.get("product_handle") or "").strip().lower()
        if _handle:
            inp["handle"] = _handle
        # Standard product category — use the stored taxonomy gid if we have it
        # (picked from the dropdown), else resolve the leaf name on the fly.
        _cat_gid = str(base.get("shopify_category_id") or "").strip() \
            or self.resolve_category_id(base.get("shopify_category"))
        if _cat_gid:
            inp["category"] = _cat_gid
        if metafields:
            inp["metafields"] = metafields
        return inp

    def _variant_input_for_create(self, item: dict) -> dict:
        """Variant dict for productCreate (uses options: [String!])."""
        grade = str(item.get("grade") or "Standard").strip()
        vi = {
            "options": [grade],
            "price": str(item.get("selling_price") or "0"),
            "inventoryItem": {"sku": str(item.get("sku") or "")},
        }
        if item.get("osp"):
            vi["compareAtPrice"] = str(item["osp"])
        if item.get("weight_grams") and item.get("weight_unit"):
            unit_map = {"g": "GRAMS", "kg": "KILOGRAMS", "lbs": "POUNDS", "lb": "POUNDS", "oz": "OUNCES"}
            vi["inventoryItem"]["measurement"] = {
                "weight": {"value": float(item["weight_grams"]),
                           "unit": unit_map.get(str(item["weight_unit"]).lower(), "GRAMS")}
            }
        return vi

    def _variant_input_for_bulk_create(self, item: dict) -> dict:
        """Variant dict for productVariantsBulkCreate (uses optionValues)."""
        grade = str(item.get("grade") or "Standard").strip()
        vi = {
            "optionValues": [{"name": grade, "optionName": "Grade"}],
            "price": str(item.get("selling_price") or "0"),
            "inventoryItem": {"sku": str(item.get("sku") or "")},
        }
        if item.get("osp"):
            vi["compareAtPrice"] = str(item["osp"])
        if item.get("weight_grams") and item.get("weight_unit"):
            unit_map = {"g": "GRAMS", "kg": "KILOGRAMS", "lbs": "POUNDS", "lb": "POUNDS", "oz": "OUNCES"}
            vi["inventoryItem"]["measurement"] = {
                "weight": {"value": float(item["weight_grams"]),
                           "unit": unit_map.get(str(item["weight_unit"]).lower(), "GRAMS")}
            }
        return vi

    def _update_variant(self, variant_id: str, item: dict):
        """Update price, SKU, weight on an existing variant."""
        vi = {
            "id": variant_id,
            "price": str(item.get("selling_price") or "0"),
            "inventoryItem": {"sku": str(item.get("sku") or "")},
        }
        if item.get("osp"):
            vi["compareAtPrice"] = str(item["osp"])
        if item.get("weight_grams") and item.get("weight_unit"):
            unit_map = {"g": "GRAMS", "kg": "KILOGRAMS", "lbs": "POUNDS", "lb": "POUNDS", "oz": "OUNCES"}
            vi["inventoryItem"]["measurement"] = {
                "weight": {"value": float(item["weight_grams"]),
                           "unit": unit_map.get(str(item["weight_unit"]).lower(), "GRAMS")}
            }
        product_id = self.graphql(
            "query V($id: ID!) { productVariant(id: $id) { product { id } } }",
            {"id": variant_id},
        )["productVariant"]["product"]["id"]
        mutation = """
        mutation UpdateVariant($productId: ID!, $variants: [ProductVariantsBulkInput!]!) {
          productVariantsBulkUpdate(productId: $productId, variants: $variants) {
            productVariants { id sku price }
            userErrors { field message }
          }
        }
        """
        result = self.graphql(mutation, {"productId": product_id, "variants": [vi]})["productVariantsBulkUpdate"]
        if result["userErrors"]:
            raise ShopifyAPIError(f"Update variant: {result['userErrors']}")

    # ── Group upsert (multiple grades → one Shopify product) ──────────────────

    def upsert_product_group(self, items: list, alt_overrides: dict = None) -> dict:
        """
        Push a group of same-product rows (different grades) as a single Shopify
        product with Grade variants using the productSet mutation (2024-01+).

        Grouping key: same item_name + model_name + brand.
        """
        if not items:
            raise ValueError("items list is empty.")

        items = sorted(items, key=lambda r: str(r.get("grade") or ""))

        base = max(items, key=lambda r: len(_parse_json_list(r.get("images_json"))))

        all_images = []
        seen = set()
        for row in items:
            for img in _parse_json_list(row.get("images_json")):
                if img and img not in seen:
                    all_images.append(img)
                    seen.add(img)
            # Per-variant image is a separate field — include it so it uploads too.
            vimg = str(row.get("variant_image") or "").strip()
            if vimg and vimg not in seen:
                all_images.append(vimg)
                seen.add(vimg)

        # Check if any SKUs already exist (to know if this is create or update)
        existing_skus = set()
        product_id = None
        for item in items:
            sku = str(item.get("sku") or "").strip()
            if not sku:
                continue
            v = self.find_variant_by_sku(sku)
            if v:
                existing_skus.add(sku)
                product_id = v["product"]["id"]

        def _uniq(seq):
            out = []
            for x in seq:
                if x not in out:
                    out.append(x)
            return out

        _all_grades = _uniq(str(i.get("grade") or "").strip() for i in items if str(i.get("grade") or "").strip())
        product_input = self._build_product_input(base, all_grades=_all_grades)

        # Build productSet input. Grade alone is NOT a unique option when two
        # variants share a grade (e.g. same grade, different colour), which makes
        # productSet reject duplicate option combos. Add a Color option whenever
        # any variant carries a colour so every variant has a unique combination.
        has_colour = any(self._colour_label(i) for i in items)

        grade_vals = _uniq(str(i.get("grade") or "Standard").strip() for i in items)
        product_options = [{"name": "Grade", "position": 1,
                            "values": [{"name": g} for g in grade_vals]}]
        if has_colour:
            colour_vals = _uniq(self._colour_label(i) or "Default" for i in items)
            product_options.append({"name": "Color", "position": 2,
                                    "values": [{"name": c} for c in colour_vals]})

        set_input = {
            **product_input,
            "productOptions": product_options,
            "variants": [self._variant_input_for_set(i, has_colour) for i in items],
        }
        if product_id:
            set_input["id"] = product_id

        mutation = """
        mutation ProductSet($input: ProductSetInput!) {
          productSet(synchronous: true, input: $input) {
            product {
              id
              variants(first: 50) { nodes { id sku inventoryItem { id } } }
            }
            userErrors { field message code }
          }
        }
        """
        result = self.graphql(mutation, {"input": set_input})["productSet"]
        if result["userErrors"]:
            raise ShopifyAPIError(f"productSet: {result['userErrors']}")

        product     = result["product"]
        product_id  = product["id"]
        sku_to_inv  = {
            v["sku"]: v["inventoryItem"]["id"]
            for v in product["variants"]["nodes"]
            if v.get("sku") and v.get("inventoryItem")
        }
        sku_to_vid  = {
            v["sku"]: v["id"]
            for v in product["variants"]["nodes"]
            if v.get("sku") and v.get("id")
        }

        created_skus = [str(i.get("sku","")) for i in items if str(i.get("sku","")) not in existing_skus]
        updated_skus = [str(i.get("sku","")) for i in items if str(i.get("sku","")) in existing_skus]

        img_result = self.add_images(product_id, all_images, alt_overrides)

        # Pin each variant's own image to that variant (best-effort — never block the push).
        media_by_alt = img_result.get("media_by_alt", {})
        variant_media = []
        for item in items:
            vimg = str(item.get("variant_image") or "").strip()
            if not vimg:
                continue
            mid = media_by_alt.get(self._img_alt(vimg, alt_overrides))
            vid = sku_to_vid.get(str(item.get("sku") or "").strip())
            if mid and vid:
                variant_media.append({"variantId": vid, "mediaIds": [mid]})

        inv_notes = {}
        if variant_media:
            try:
                # Freshly-uploaded media process asynchronously — wait until READY
                # before pinning them to variants.
                self._wait_media_ready([mid for vm in variant_media for mid in vm["mediaIds"]])
                self.append_variant_media(product_id, variant_media)
            except Exception as _vm_err:
                inv_notes["_variant_images"] = f"variant image assign failed: {_vm_err}"

        # Warehouse metafield — set AFTER productSet, only for variants with a value,
        # so a blank/choices mismatch can never block the product push.
        _valid_wh = self.warehouse_choices()
        wh_metafields = []
        for item in items:
            _wh = _clean(item.get("warehouse_location"))
            _vid = sku_to_vid.get(str(item.get("sku") or "").strip())
            if not (_wh and _vid):
                continue
            if _valid_wh and _wh not in _valid_wh:
                # Value isn't an allowed choice on Shopify — skip it (never block).
                inv_notes.setdefault("_warehouse", f"skipped invalid value(s) e.g. {_wh!r}")
                continue
            wh_metafields.append({"ownerId": _vid, "namespace": "custom",
                                  "key": "warehouse_location",
                                  "type": "single_line_text_field", "value": _wh})
        if wh_metafields:
            try:
                self.set_metafields(wh_metafields)
            except Exception as _wh_err:
                inv_notes["_warehouse"] = f"warehouse metafield failed: {_wh_err}"

        inventory_ok = []   # SKUs whose inventory actually applied (set or adjust)
        for item in items:
            sku   = str(item.get("sku","")).strip()
            qty   = int(item.get("quantity") or 0)
            inv_id = sku_to_inv.get(sku)
            if not inv_id:
                # Variant synced but we never got an inventory item id back — inventory
                # did NOT apply; do not treat it as a confirmed inventory success.
                inv_notes[sku] = "no inventory item id returned"
                continue
            if sku in existing_skus:
                # Existing variant → ADD only the new units (delta) so Shopify-side
                # sales since the last push are preserved.
                delta = int(item.get("inventory_delta") or 0)
                note = self._try_adjust_inventory(inv_id, delta) if delta else ""
            else:
                # Brand-new variant → set the initial on-hand quantity.
                note = self._try_set_inventory(inv_id, qty) if qty >= 0 else ""
            if note:
                inv_notes[sku] = note
            else:
                inventory_ok.append(sku)

        numeric_id = product_id.rsplit("/", 1)[-1]
        result_out = {
            "success": True,
            "product_url": f"https://{self.store}/admin/products/{numeric_id}",
            "created": created_skus,
            "updated": updated_skus,
            "inventory_ok": inventory_ok,
            "images": img_result,
        }
        if inv_notes:
            result_out["inventory_notes"] = inv_notes
        return result_out

    @staticmethod
    def _colour_label(item: dict) -> str:
        """Readable colour label for a variant, from colours_json or colour_code."""
        raw = item.get("colours_json")
        try:
            cs = json.loads(raw) if isinstance(raw, str) else (raw or [])
        except Exception:
            cs = []
        if isinstance(cs, list) and cs:
            return ", ".join(str(c) for c in cs)
        return str(item.get("colour_code") or "").strip()

    def _variant_input_for_set(self, item: dict, has_colour: bool = False) -> dict:
        """Variant dict for productSet mutation."""
        grade = str(item.get("grade") or "Standard").strip()
        option_values = [{"optionName": "Grade", "name": grade}]
        if has_colour:
            option_values.append({"optionName": "Color",
                                  "name": self._colour_label(item) or "Default"})
        vi = {
            "optionValues": option_values,
            "price": str(item.get("selling_price") or "0"),
            "taxable": False,   # #7 — never charge tax on the variant
            "inventoryItem": {"sku": str(item.get("sku") or ""), "tracked": True},
        }
        # #8 — unit cost maps to our purchase price
        _cost = _clean(item.get("purchase_price"))
        if _cost:
            vi["inventoryItem"]["cost"] = _cost
        # #9 — HS (harmonized system) code: Shopify requires 6–13 digits. Only send
        # when valid, so a bad entry can't fail the whole atomic productSet.
        _hs = re.sub(r"\D", "", _clean(item.get("hs_code")))
        if 6 <= len(_hs) <= 13:
            vi["inventoryItem"]["harmonizedSystemCode"] = _hs
        if item.get("osp"):
            vi["compareAtPrice"] = str(item["osp"])
        if item.get("weight_grams") and item.get("weight_unit"):
            unit_map = {"g": "GRAMS", "kg": "KILOGRAMS", "lbs": "POUNDS", "lb": "POUNDS", "oz": "OUNCES"}
            vi["inventoryItem"]["measurement"] = {
                "weight": {"value": float(item["weight_grams"]),
                           "unit": unit_map.get(str(item["weight_unit"]).lower(), "GRAMS")}
            }
        # NOTE: the warehouse_location metafield is intentionally NOT set here —
        # it's a choices metafield and including it in the atomic productSet blocks
        # the whole product when any variant is blank. It's set separately after.
        return vi

    # ── Single-item upsert (kept for backwards compat) ────────────────────────

    def upsert_product(self, item: dict) -> dict:
        """Single-row push — wraps upsert_product_group for one item."""
        result = self.upsert_product_group([item])
        sku    = str(item.get("sku",""))
        action = "created" if sku in result["created"] else "updated"
        return {
            "success": True,
            "action": action,
            "sku": sku,
            "product_id": "",
            "product_url": result["product_url"],
            "images": result["images"],
            **({"inventory_note": list(result["inventory_notes"].values())[0]}
               if result.get("inventory_notes") else {}),
        }
