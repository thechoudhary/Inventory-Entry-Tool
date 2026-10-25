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
# Logs output so failures are visible in Streamlit Cloud logs.
try:
    _install = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        check=False, capture_output=True, text=True,
    )
    if _install.returncode != 0:
        logger.warning("playwright install chromium failed (rc=%s): %s",
                       _install.returncode, _install.stderr.strip())
    else:
        logger.info("playwright install chromium: %s", _install.stdout.strip() or "OK")
except Exception as _e:
    logger.warning("Could not run playwright install: %s", _e)




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

async def extract_amazon_product(product_url, proxy_api_key=None):

    product = {
        "asin": extract_asin(product_url),
        "url": product_url,
        "title": "",
        "price": "",
        "rating": "",
        "reviews": "",
        "availability": "",
        "description": "",
        "amazon_about_this_item": "",
        "specifications": {},
        "images": [],
        "error": "",
    }

    # ScraperAPI proxy — routes through residential IPs to bypass cloud IP blocks.
    # Set SCRAPERAPI_KEY in Streamlit secrets (or env) to enable; omit for local runs.
    proxy = None
    if proxy_api_key:
        proxy = {
            "server":   "http://proxy-server.scraperapi.com:8001",
            "username": "scraperapi",
            "password": proxy_api_key,
        }
        logger.info("Using ScraperAPI proxy for this request.")

    async with async_playwright() as p:

        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--disable-extensions",
                "--window-size=1440,1000",
            ],
        )

        context_kwargs = dict(
            locale="en-IN",
            viewport={"width": 1440, "height": 1000},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            ignore_https_errors=True,
        )
        if proxy:
            context_kwargs["proxy"] = proxy

        context = await browser.new_context(**context_kwargs)

        # Remove headless/automation signals that Amazon detects
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            Object.defineProperty(navigator, 'languages', { get: () => ['en-IN', 'en-US', 'en'] });
            window.chrome = { runtime: {} };
        """)

        page = await context.new_page()

        try:
            response = await page.goto(
                product_url,
                wait_until="domcontentloaded",
                timeout=60000,
            )

            # Wait for the product title rather than a fixed delay
            try:
                await page.wait_for_selector("#productTitle", timeout=12000)
            except Exception:
                pass  # proceed even if title selector is slow

            logger.info("HTTP status: %s", response.status if response else "Unknown")
            page_title = (await page.title()).lower()
            current_url = page.url
            logger.info("Page title: %s | URL: %s", page_title, current_url)

            # Detect bot checks — Amazon uses several patterns
            if (
                "robot check" in page_title
                or "captcha" in page_title
                or "/errors/validatecaptcha" in current_url
                or "api-services" in current_url
                or "auth-redirect" in current_url
                or page_title in ("", "amazon.in")
            ):
                msg = f"Amazon returned a bot/access check (title='{page_title}', url={current_url}) — could not fetch product details."
                logger.warning(msg)
                product["error"] = msg
                return product

            # Product title and pricing.
            product["title"] = await get_text(page, "#productTitle")

            # If the title is empty the page probably didn't load proper product content
            if not product["title"]:
                msg = f"Product page loaded but no title found (page='{page_title}'). Amazon may be blocking this request from cloud IPs."
                logger.warning(msg)
                product["error"] = msg
                return product

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
            product["images"] = await scrape_images_from_page(page)

        except Exception as exc:
            msg = f"Scrape failed: {exc}"
            logger.exception("Product extraction failed")
            product["error"] = msg

        finally:
            await browser.close()

    return product


# ------------------------------
# Images
# ------------------------------

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


async def scrape_images_from_page(page):

    image_urls = set()

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

    # Batch-extract gallery image URLs via a single JS call instead of
    # iterating each thumbnail in Python (cuts many round-trips).
    gallery_urls = await page.evaluate("""() => {
        const gallery = document.querySelector('#altImages');
        if (!gallery) return [];
        const results = [];
        gallery.querySelectorAll('img').forEach(img => {
            const li = img.closest('li');
            if (li) {
                const cls = li.className.toLowerCase();
                if (cls.includes('video') || cls.includes('swatch') || cls.includes('spinner')) return;
            }
            ['data-old-hires', 'data-a-dynamic-image', 'src'].forEach(attr => {
                const val = img.getAttribute(attr);
                if (!val) return;
                if (attr === 'data-a-dynamic-image') {
                    try {
                        const d = JSON.parse(val);
                        if (typeof d === 'object') Object.keys(d).forEach(u => results.push(u));
                    } catch (_) {}
                } else {
                    results.push(val);
                }
            });
        });
        return results;
    }""")
    image_urls.update(gallery_urls or [])
    logger.debug("Product gallery elements (raw): %d", len(gallery_urls or []))

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

    logger.info("Found %d unique image URLs.", len(image_urls))

    if not image_urls:
        logger.info("No gallery images found. Amazon may be using a different gallery.")
        return []

    return image_urls


async def get_spec(page):
    # Give specs a chance to appear (but don't wait if they're already there)
    try:
        await page.wait_for_selector(
            "tr[class*='po-'], #productOverview_feature_div tr, "
            "#productDetails_techSpec_section_1 tr",
            timeout=5000,
        )
    except Exception:
        pass

    # Extract all key/value pairs in a single JS round-trip for speed
    return await page.evaluate("""() => {
        const result = {};
        const selectorGroups = [
            'tr[class*="po-"]',
            '#productOverview_feature_div tr',
            '#productDetails_techSpec_section_1 tr',
            '#productDetails_detailBullets_sections1 tr',
            '#productDetails_db_sections tr',
            '#poExpander tr',
        ];
        for (const sel of selectorGroups) {
            const rows = document.querySelectorAll(sel);
            if (!rows.length) continue;
            rows.forEach(row => {
                const keyEl = row.querySelector('span.a-text-bold, th');
                const valSpan = row.querySelector('span.po-break-word');
                const cells = row.querySelectorAll('td');
                let key = '', val = '';
                if (keyEl) key = keyEl.innerText.trim().replace(/:$/, '');
                if (valSpan) {
                    val = valSpan.innerText.trim();
                } else if (cells.length >= 2) {
                    key = cells[0].innerText.trim().replace(/:$/, '');
                    val = cells[1].innerText.trim();
                }
                if (key && val && key !== val) result[key] = val;
            });
            if (Object.keys(result).length) break;
        }
        return result;
    }""")


# --------------------------------------------------
# PUBLIC SYNC API
# --------------------------------------------------


def fetch_amazon_product(product_url, proxy_api_key=None):
    """Run the async scraper in an isolated worker thread with its own event loop.

    Pass proxy_api_key (ScraperAPI key) to route through residential IPs —
    required when running on Streamlit Cloud or any AWS/GCP/Azure host that
    Amazon blocks at the IP level.
    """
    def _run():
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(extract_amazon_product(product_url, proxy_api_key))
        finally:
            loop.close()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(_run).result()
