# Inventory Entry Tool (Streamlit + SQLite)

## Features
- Add products with Item Name, Model Name, Item Category, Brand, Grade, Colour, MRP, Purchase Price, and Selling Price.
- Manage Category, Brand, Colour, and Grade dropdown values in a separate section.
- Each master value requires a name and a unique code; the creation timestamp is saved automatically.
- Generates SKU in this format: `CATEGORY-BRAND-MODEL_ABV-COLOUR-GRADE`.
- Saves data to a local SQLite database (`inventory.db`).
- Search inventory and export visible results to CSV.

## Run locally

Python 3.10+ recommended.

```bash
python -m venv .venv
source .venv/bin/activate       # macOS/Linux
# .venv\Scripts\activate        # Windows

pip install -r requirements.txt
streamlit run app.py
```

Streamlit will print a local URL (usually http://localhost:8501).

## SKU abbreviation rule
- One-word model name: first 8 alphanumeric characters.
- Multi-word model name: first 2 characters of each word, up to 10 characters.
- Example: category `WM`, brand `WH`, model `Magic Clean 7 GenX`, colour `GRY`, grade `A`
  becomes `WM-WH-MACL7G-GRY-A` (exact abbreviation depends on the model's words).

## Important notes
- SQLite is appropriate for a small, single-user or light-use internal tool. For many simultaneous users or cloud hosting, consider PostgreSQL.
- Back up `inventory.db` regularly; it contains your saved data.
- SKU uniqueness is enforced by SQLite. If the generated SKU already exists, the product will not be saved.
