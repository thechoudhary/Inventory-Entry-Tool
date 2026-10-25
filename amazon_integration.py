"""Amazon product scraper using headless Playwright/Chromium."""

import os
import re
import json
import asyncio
import logging
import subprocess
import sys
import concurrent.futures

import pandas as pd

from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright

logger = logging.getLogger(__name__)

# Install Chromium browser binary if not already present (needed on Streamlit Cloud).
# This is idempotent — Playwright skips the download when the binary already exists.
try:
    subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=False, capture_output=True,
    )
except Exception:
    pass


"""Adapter for the user's existing Amazon product scraper.

Place the scraper module beside app.py as amazon_scraper.py and expose either:
  - extract_amazon_product(product_url)  (sync or async), or
  - fetch_product_details(product_url)   (sync or async).
The function should return the product dictionary described in the README.
"""

import os
import re
import json
import asyncio
import aiohttp
import pandas as pd

from pathlib import Path
from urllib.parse import urlparse
from playwright.async_api import async_playwright







async def get_text(page, selector):
    try:
        element = page.locator(selector).first
        if await element.count():
            return (await element.inner_text(timeout=3000)).strip()
    except Exception:
        pass

    return ""


def extract_asin(url):
    match = re.search(
        r"/(?:dp|gp/product)/([A-Z0-9]{10})",
        url,
        re.IGNORECASE,
    )
    return match.group(1).upper() if match else ""


def normalize_image_url(url):
    """Remove Amazon thumbnail size suffixes when present."""
    if not url or not url.startswith("https://"):
        return ""

    if "images-amazon.com/images/I/" not in url:
        return ""

    # Convert thumbnail variants such as ._SX38_SY50_.jpg
    # to the base image URL where possible.
    return re.sub(
        r"\._[A-Z0-9_,]+_\.(jpg|jpeg|png|webp)(?:\?.*)?$",
        r".\1",
        url,
        flags=re.IGNORECASE,
    )







async def extract_about_this_item(page):
    selectors = [
        "#feature-bullets ul li span.a-list-item",
        "#feature-bullets .a-list-item",
        "#featurebullets_feature_div li",
    ]

    bullets = []
    seen = set()

    for selector in selectors:
        elements = page.locator(selector)

        for i in range(await elements.count()):
            try:
                text = (await elements.nth(i).inner_text()).strip()

                if (
                    text
                    and text not in seen
                    and len(text) > 5
                ):
                    seen.add(text)
                    bullets.append(text)

            except Exception:
                continue

        if bullets:
            break

    return bullets








# --------------------------------------------------
# MAIN EXTRACTION FUNCTION
# --------------------------------------------------

async def extract_amazon_product(product_url):

    product = {
        "asin": extract_asin(product_url),
        "url": product_url,
        "title": "",
        "price": "",
        "rating": "",
        "reviews": "",
        "availability": "",
        "description": "",
        "amazon_about_this_item":"",
        "specifications": {},
        "images": [],
        # "downloaded_images": [],
    }

    async with async_playwright() as p:

        browser = await p.chromium.launch(headless=True)

        page = await browser.new_page(
            locale="en-IN",
            viewport={"width": 1440, "height": 1000},
        )

        try:
            response = await page.goto(
                product_url,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            await page.wait_for_timeout(3000)

            print("HTTP status:", response.status if response else "Unknown")
            print("Page title:", await page.title())

            page_title = (await page.title()).lower()

            if (
                "robot check" in page_title
                or "captcha" in page_title
                or "/errors/validatecaptcha" in page.url
            ):
                print(
                    "Amazon returned an access check. "
                    "No attempt will be made to bypass it."
                )
                return product

            # Product title and pricing.
            product["title"] = await get_text(page, "#productTitle")

            product["price"] = (
                await get_text(page, ".priceToPay")
                or await get_text(
                    page,
                    "#corePriceDisplay_desktop_feature_div",
                )
                or await get_text(page, "#priceblock_ourprice")
                or await get_text(page, "#priceblock_dealprice")
            )

            product["rating"] = (
                await get_text(page, "#acrPopover")
                or await get_text(
                    page,
                    "span[data-hook='rating-out-of-text']",
                )
            )

            product["reviews"] = await get_text(
                page, "#acrCustomerReviewText"
            )

            product["availability"] = await get_text(
                page, "#availability"
            )

            product["description"] = (
                await get_text(page, "#productDescription")
                or await get_text(page, "#feature-bullets")
            )


            about_items = await extract_about_this_item(page)

            product["amazon_about_this_item"] = json.dumps(
                about_items,
                ensure_ascii=False
            )
            
            product["specifications"] = await get_spec(page)
                        # Capture gallery images.
            product["images"] = await scrape_images_from_page(page)

           
        except Exception as exc:
            print("Product extraction failed:", repr(exc))

        finally:
            await browser.close()

    return product


#-------------------------------
#.  Images
#--------------------------------




DOWNLOAD_DIR = "amazon_product_images"


def get_high_res_url(url):
    """Convert Amazon image variants to a high-resolution URL."""
    if not url:
        return None

    # Replace Amazon sizing suffixes, e.g. _SX679_ or _SX342_
    # with _SL1500_ to request a larger image.
    return re.sub(
        r"\._[A-Z0-9_,]+_\.(jpg|jpeg|png|webp)(?:\?.*)?$",
        r"._SL1500_.\1",
        url,
        flags=re.IGNORECASE,
    )


# async def download_image(session, url, file_path):
#     try:
#         timeout = aiohttp.ClientTimeout(total=30)

#         async with session.get(url, timeout=timeout) as response:
#             if response.status != 200:
#                 print(f"HTTP {response.status}: {url}")
#                 return False

#             content_type = response.headers.get(
#                 "Content-Type", ""
#             ).lower()

#             if not content_type.startswith("image/"):
#                 print(f"Not an image: {url}")
#                 return False

#             content = await response.read()

#             with open(file_path, "wb") as f:
#                 f.write(content)

#             print(f"Downloaded: {os.path.basename(file_path)}")
#             return True

#     except Exception as exc:
#         print(f"Failed: {url} | {exc}")
#         return False


async def scrape_images_from_page(page):

    image_urls = set()

    async with async_playwright() as p:
        
        main_image = page.locator(
            "#landingImage, #imgTagWrapperId img"
        ).first

        if await main_image.count():
            for attr in ["data-old-hires", "data-a-dynamic-image", "src"]:
                value = await main_image.get_attribute(attr)

                if not value:
                    continue

                if attr == "data-a-dynamic-image":
                    try:
                        data = json.loads(value)
                        if isinstance(data, dict):
                            image_urls.update(data.keys())
                    except (json.JSONDecodeError, TypeError):
                        pass
                else:
                    image_urls.add(value)

        # 2. Extract images only from the product gallery.
        # Do not scan every img element on the page.
        gallery = page.locator("#altImages")

        if await gallery.count():
            gallery_images = gallery.locator("img")
            print(
                "Product gallery elements:",
                await gallery_images.count(),
            )

            for i in range(await gallery_images.count()):
                img = gallery_images.nth(i)

                try:
                    parent_class = await img.evaluate("""
                        el => {
                            const li = el.closest('li');
                            return li ? li.className : '';
                        }
                    """)

                    if any(
                        word in parent_class.lower()
                        for word in ["video", "swatch", "spinner"]
                    ):
                        continue

                    for attr in [
                        "data-old-hires",
                        "data-a-dynamic-image",
                        "src",
                    ]:
                        value = await img.get_attribute(attr)

                        if not value:
                            continue

                        if attr == "data-a-dynamic-image":
                            try:
                                data = json.loads(value)
                                if isinstance(data, dict):
                                    image_urls.update(data.keys())
                            except (json.JSONDecodeError, TypeError):
                                pass
                        else:
                            image_urls.add(value)

                except Exception as exc:
                    print(f"Skipping gallery item {i + 1}: {exc}")

        

    # 3. Filter and normalize Amazon product image URLs.
    final_urls = set()

    for url in image_urls:
        if not url or not url.startswith("https://"):
            continue

        host = urlparse(url).netloc.lower()

        if not (
            "media-amazon.com" in host
            or "images-amazon.com" in host
        ):
            continue

        if any(
            word in url.lower()
            for word in ["sprite", "icon", "logo", "transparent"]
        ):
            continue

        high_res_url = get_high_res_url(url)
        final_urls.add(high_res_url or url)

    image_urls = sorted(final_urls)

    print(f"\nFound {len(image_urls)} unique image URLs.")

    if not image_urls:
        print("No gallery images found. Amazon may be using a different gallery.")
        return

   

    

  

    # print(f"\nCompleted: {success_count}/{len(image_urls)} images downloaded.")
    # print("Output folder:", Path(DOWNLOAD_DIR).resolve())

    return image_urls







async def get_spec(page):
    # Wait briefly for Amazon's product specifications to load
    await page.wait_for_timeout(2000)

    rows = page.locator(
        "tr[class*='po-'], "
        "#productOverview_feature_div tr, "
        "#productDetails_techSpec_section_1 tr, "
        "#productDetails_detailBullets_sections1 tr, "
        "#productDetails_db_sections tr, "
        "#poExpander tr"
    )
    specifications = {}
    for i in range(await rows.count()):
        row = rows.nth(i)

        # Extract the specification label
        key_locator = row.locator(
            "span.a-text-bold, th"
        ).first

        # Extract the specification value
        value_locator = row.locator(
            "span.po-break-word, td"
        )

        key = ""
        value = ""

        if await key_locator.count():
            key = (await key_locator.inner_text()).strip()

        if await value_locator.count():
            # Prefer the dedicated value span
            value_span = row.locator("span.po-break-word")

            if await value_span.count():
                value = (await value_span.first.inner_text()).strip()
            else:
                cells = row.locator("td")

                if await cells.count() >= 2:
                    key = (await cells.nth(0).inner_text()).strip()
                    value = (await cells.nth(1).inner_text()).strip()

        if key and value and key != value:
            specifications[key.rstrip(":")] = value
    return specifications

    # print("Specifications found:", len(product["specifications"]))
    # print(product["specifications"])



# --------------------------------------------------
# SAVE OUTPUT
# --------------------------------------------------




def fetch_amazon_product(product_url):

    result = asyncio.run(extract_amazon_product(product_url))
    return result


