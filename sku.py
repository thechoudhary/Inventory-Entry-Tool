import re
import unicodedata

def normalize(text):
    text = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    text = text.upper().strip()
    text = re.sub(r"[^A-Z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()

def abbreviate(text, rules=None):
    tokens = normalize(text).split()
    rules = sorted(rules or [], key=lambda r: (len(normalize(r.get("name",""))), int(r.get("priority",0))), reverse=True)
    # Phrase replacement is applied to normalized text with token boundaries, longest phrase first.
    value = " " + normalize(text) + " "
    for rule in rules:
        original = normalize(rule.get("name", ""))
        replacement = normalize(rule.get("code", ""))
        if original and replacement:
            value = re.sub(r"(?<![A-Z0-9])" + re.escape(original) + r"(?![A-Z0-9])", replacement, value)
    value = normalize(value)
    out = []
    for token in value.split():
        if token.isdigit() or re.search(r"\d", token):
            out.append(token)
        elif len(token) <= 3:
            out.append(token)
        else:
            # Deterministic fallback: first three letters. Custom rules take precedence.
            out.append(token[:3])
    return "".join(out)

def generate_sku(category_code, brand_code, item_name, model_name="", grade_code="", colour_code_value="", rules=None):
    # SKU structure: CATEGORY-BRAND-MODEL-COLOUR-GRADE.
    # Use model_name when supplied, otherwise fall back to item_name.
    model_component = model_name.strip() if model_name and model_name.strip() else item_name
    parts = [
        normalize(category_code),
        normalize(brand_code),
        abbreviate(model_component, rules),
        normalize(colour_code_value),
        normalize(grade_code),
    ]
    parts = [re.sub(r"[^A-Z0-9]", "", p) for p in parts if p]
    sku = "-".join(parts)
    if not sku or not re.fullmatch(r"[A-Z0-9]+(?:-[A-Z0-9]+)*", sku):
        raise ValueError("Unable to generate a valid SKU from these attributes.")
    return sku

def generate_product_handle(category_code, brand_code, item_name, model_name="", rules=None):
    # Product handle = SKU without colour and grade: CATEGORY-BRAND-MODEL.
    # Shared by every variant of the same product, so it is a stable product id.
    model_component = model_name.strip() if model_name and model_name.strip() else item_name
    parts = [
        normalize(category_code),
        normalize(brand_code),
        abbreviate(model_component, rules),
    ]
    parts = [re.sub(r"[^A-Z0-9]", "", p) for p in parts if p]
    handle = "-".join(parts)
    if not handle or not re.fullmatch(r"[A-Z0-9]+(?:-[A-Z0-9]+)*", handle):
        raise ValueError("Unable to generate a valid product handle from these attributes.")
    return handle

def colour_code(colours):
    return "".join(normalize(c).replace(" ", "") for c in sorted(set(colours), key=lambda x: x.casefold()))


def extract_model_from_specs(specs, fallback="", extra_text=""):
    """Pull a model identifier (never the year). Looks in the specs dict first,
    then scans free text (e.g. the 'Model/Year : …' bullet in About-this-item),
    and finally the fallback. Any 4-digit year is stripped from the result."""
    val = ""
    if isinstance(specs, dict):
        _wanted = ("model", "model name", "model number", "model name/number",
                   "item model number", "manufacturer model", "model no")
        for k, v in specs.items():
            if str(k).strip().casefold() in _wanted and str(v).strip():
                val = str(v).strip()
                break
    if not val and extra_text:
        m = re.search(r"model(?:\s*/\s*year)?\s*[:\-]\s*([^\n\r;|]+)", str(extra_text), re.I)
        if m:
            val = m.group(1).strip()
    if not val:
        val = str(fallback or "").strip()
    # Drop standalone 4-digit years (19xx / 20xx) and tidy separators.
    val = re.sub(r"\b(?:19|20)\d{2}\b", "", val)
    val = re.sub(r"\s{2,}", " ", val).strip(" -,/")
    return val


def extract_physical_from_specs(specs):
    """Derive weight and packed dimensions from a specifications dict.

    Handles Amazon-style formats like "58D x 55W x 85H Centimeters",
    "30.5 x 20.3 x 15.2 cm" and weights like "1.5 kg" / "500 grams".
    Returns a dict with weight_grams, weight_unit and packed_length/width/height
    (any field that can't be parsed stays None).
    """
    out = {"weight_grams": None, "weight_unit": None,
           "packed_length": None, "packed_width": None, "packed_height": None}
    if not isinstance(specs, dict):
        return out

    for wkey in ("Item Weight", "item_weight", "Weight", "Net Weight", "Product Weight"):
        wr = specs.get(wkey)
        if wr:
            parts = str(wr).strip().split()
            if len(parts) >= 2:
                try:
                    out["weight_grams"] = float(parts[0])
                    out["weight_unit"]  = parts[1].lower()
                except ValueError:
                    pass
            break

    # Allow optional letters (e.g. D/W/H) between each number and the separator.
    dim_re = re.compile(
        r"([\d.]+)\s*[a-zA-Z]*\s*[xX×]\s*([\d.]+)\s*[a-zA-Z]*\s*[xX×]\s*([\d.]+)")
    for dkey in ("Product Dimensions", "product_dimensions", "Item Dimensions",
                 "Package Dimensions", "Dimensions"):
        dr = specs.get(dkey)
        if dr:
            m = dim_re.search(str(dr))
            if m:
                try:
                    out["packed_length"] = float(m.group(1))
                    out["packed_width"]  = float(m.group(2))
                    out["packed_height"] = float(m.group(3))
                except ValueError:
                    pass
            break

    return out
