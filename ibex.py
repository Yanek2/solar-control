"""
Fetches the current-hour SDAC price from the IBEX Bulgaria website.
Tries a plain HTTP request first; falls back to Playwright if the page
needs JavaScript to render the table.
"""

import asyncio
import re
import logging
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from config import IBEX_URL

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "bg-BG,bg;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": "https://ibex.bg/",
}


def _parse_float(text: str) -> float | None:
    cleaned = re.sub(r"[^\d.,-]", "", text.strip())
    cleaned = cleaned.replace(",", ".")
    # keep only last dot if there are multiple (thousand-separator cleanup)
    parts = cleaned.split(".")
    if len(parts) > 2:
        cleaned = "".join(parts[:-1]) + "." + parts[-1]
    try:
        return float(cleaned)
    except ValueError:
        return None


def _extract_price_from_html(html: str) -> float | None:
    soup = BeautifulSoup(html, "lxml")

    # The page has multiple tables; find the 15-minute period table (has "Период" in headers)
    table = None
    for t in soup.find_all("table"):
        t_rows = t.find_all("tr")
        if not t_rows:
            continue
        t_headers = [c.get_text(strip=True) for c in t_rows[0].find_all(["th", "td"])]
        if any("Период" in h for h in t_headers):
            table = t
            break

    if not table:
        logger.warning("No period table found on IBEX page")
        return None

    rows = table.find_all("tr")
    if not rows:
        return None

    # Identify column indices from header
    header_cells = rows[0].find_all(["th", "td"])
    headers = [c.get_text(strip=True) for c in header_cells]
    logger.debug("IBEX table headers: %s", headers)

    price_col = next(
        (i for i, h in enumerate(headers) if "Цена" in h or "EUR" in h), 2
    )
    period_col = next(
        (i for i, h in enumerate(headers) if "Период" in h), 1
    )

    # IBEX table uses CET (UTC+1, no DST) for delivery period times,
    # regardless of local DST — so in summer Sofia (EEST=UTC+3) is 2h ahead.
    from datetime import timezone, timedelta
    cet_tz = timezone(timedelta(hours=1))
    now_cet = datetime.now(tz=cet_tz)
    current_hour = now_cet.hour
    quarter_start = (now_cet.minute // 15) * 15
    period_prefix = f"{current_hour:02d}:{quarter_start:02d}"
    logger.debug("Looking for IBEX period starting with '%s' (CET)", period_prefix)

    fallback_price = None

    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) <= max(price_col, period_col):
            continue

        price_text = cells[price_col].get_text(strip=True)
        price = _parse_float(price_text)
        if price is None:
            continue

        # Save first valid price as fallback
        if fallback_price is None:
            fallback_price = price

        # Match the current 15-minute period by start time
        period_text = cells[period_col].get_text(strip=True)
        if period_text.startswith(period_prefix):
            logger.info("Price for period '%s': %.2f EUR/MWh", period_text, price)
            return price

    if fallback_price is not None:
        logger.warning(
            "No row matched period %s — returning first available price: %.2f",
            period_prefix,
            fallback_price,
        )
    return fallback_price


async def get_current_price() -> float | None:
    """Return the IBEX SDAC price (EUR/MWh) for the current delivery hour."""
    try:
        resp = requests.get(IBEX_URL, headers=_HEADERS, timeout=30)
        resp.raise_for_status()
        price = _extract_price_from_html(resp.text)
        if price is not None:
            return price
        logger.warning("requests fetch succeeded but no price found — trying Playwright")
    except requests.RequestException as exc:
        logger.warning("requests fetch failed (%s) — trying Playwright", exc)

    try:
        return await asyncio.wait_for(_get_price_via_playwright(), timeout=60)
    except asyncio.TimeoutError:
        logger.error("Playwright IBEX fallback timed out after 60s — giving up")
        return None


async def _get_price_via_playwright() -> float | None:
    """Headless Playwright fallback — uses playwright-stealth to pass bot challenges."""
    try:
        import sys
        from playwright.async_api import async_playwright

        extra_args = ["--no-sandbox", "--disable-dev-shm-usage"] if sys.platform != "win32" else []

        try:
            from playwright_stealth import Stealth
            stealth = Stealth()
        except ImportError:
            stealth = None

        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True, args=extra_args + [
                "--disable-gpu", "--disable-extensions", "--no-zygote",
            ])
            context = await browser.new_context(
                user_agent=_HEADERS["User-Agent"],
                viewport={"width": 1366, "height": 768},
                locale="bg-BG",
            )
            page = await context.new_page()
            if stealth:
                await stealth.apply_stealth_async(page)
            await page.goto(IBEX_URL, wait_until="networkidle", timeout=30000)
            # SuperJS redirects the page after networkidle — wait for the real table
            import asyncio
            await page.wait_for_selector("table", timeout=20000)
            await asyncio.sleep(1)
            html = await page.content()
            await browser.close()

        return _extract_price_from_html(html)
    except Exception as exc:
        logger.error("Playwright IBEX fallback failed: %s", exc)
        return None
