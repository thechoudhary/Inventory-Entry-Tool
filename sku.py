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

def colour_code(colours):
    return "".join(normalize(c).replace(" ", "") for c in sorted(set(colours), key=lambda x: x.casefold()))
