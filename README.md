# Inventory Entry & SKU Management

A Streamlit + SQLite inventory application with login, admin/standard roles, master data, configurable abbreviations, SKU previews, multi-colour variants, quantity merging, search, editing and audit history.

## Requirements
- Python 3.10+
- macOS, Windows or Linux

## Setup (macOS / Linux)
```bash
cd inventory_sku_app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python setup_admin.py
streamlit run app.py
```

Open the local URL shown by Streamlit (normally `http://localhost:8501`).

## First-use checklist
1. Sign in with the administrator created by `setup_admin.py`.
2. Open **Manage** and add categories, brands, grades (if needed), colours and abbreviations.
3. Codes are used in SKU generation. Set explicit codes when business naming rules require them.
4. Use **Add Inventory** to enter a product, preview each colour variant and confirm.
5. The same SKU and matching defining attributes add to existing quantity; conflicting attributes are blocked.
6. Use **User Management** to create standard users.

## Current SKU rule
The current configurable pattern is `CATEGORY-BRAND-MODEL-COLOUR-GRADE`. Category, brand and grade use their master-data codes. Item/model words use exact configured abbreviation phrases first; otherwise digits are preserved and unmatched words longer than three characters fall back to their first three letters. Colour code is the configured colour code for each selected colour. This is a documented starting rule and should be adjusted to your approved SKU examples before production.

For multiple selected colours, this app creates one inventory record and one SKU. Selected colours are sorted alphabetically by colour name and their configured codes are concatenated into one colour segment (for example, Blue=BLU and Red=RED gives `BLURED`). Quantity applies to the combined-colour product record.

## Database
SQLite is stored in `inventory.db` beside the application by default. To use a different persistent location, set `INVENTORY_DB_PATH` before launching the app. Keep this file backed up and private. Startup uses non-destructive schema checks; do not delete the database to upgrade.

## Important limitations
- Standard users can view and add inventory. Editing existing records, master data, user management and audit history are admin-only.
- This is a useful small-team starter app, not a hardened public-facing enterprise service. Use HTTPS, persistent storage and access controls if hosting it on a network.
- Multi-user concurrent writes are protected with SQLite transactions and unique SKU constraints, but evaluate PostgreSQL for sustained concurrent use.
- Passwords use Python's built-in scrypt password hashing.
- The app does not scrape source URLs or connect to Shopify/Zoho.


## Bulk master-data import
In **Manage**, choose a master-data type and upload a CSV with `name` and `code` columns. Header matching is case-insensitive, so `Name,CODE` is accepted. Both values are required. Duplicate names or codes are skipped case-insensitively, and the result reports which rows were imported or skipped. For abbreviations, `name` is the original word/phrase and `code` is its abbreviation.

Example:
```csv
name,code
Electronics,ELE
Home Appliances,HA
Clothing,CLT
```


Inventory records also support an optional LOT Number, which is stored separately from the generated SKU.


## Recent Add Inventory updates

- Added integer **OST** and **Capacity**, plus a capacity-unit dropdown.
- Item name and model name have searchable suggestions from existing inventory records.
- When a new SKU has an Amazon/source URL, the app calls your existing scraper and opens a review step before saving.
- Amazon title, ASIN, price, rating, review count, availability, description, “About this item”, and specifications can be reviewed/edited. Images are shown as thumbnails without displaying their URLs; drag and drop image tiles to reorder them, remove unwanted images, or use the + tile to add an image URL.
- Amazon metadata, specifications and ordered image URLs are stored in the existing SQLite `inventory` table. Existing database files are migrated non-destructively.

### Connect your existing Amazon scraper

Place your existing scraper module at `amazon_scraper.py` beside `app.py`. It should expose `extract_amazon_product(product_url)` (sync or async) or `fetch_product_details(product_url)` and return a dictionary with keys such as `asin`, `url`, `title`, `price`, `rating`, `reviews`, `availability`, `description`, `amazon_about_this_item`, `specifications` (dictionary), and `images` (list of image URLs). The adapter is in `amazon_integration.py`.

Install dependencies and the Playwright browser if your scraper uses Playwright:

```bash
pip install -r requirements.txt
playwright install chromium
```

If the scraper module is not present, the app still permits manual entry and image URL ordering; it displays a warning instead of blocking the inventory workflow. Amazon details are fetched only when the generated SKU is not already present.
