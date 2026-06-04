"""Fetch and print all IBEX hourly prices from the current table."""
import asyncio
from playwright.async_api import async_playwright
try:
    from playwright_stealth import Stealth
    _STEALTH = Stealth()
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False
from bs4 import BeautifulSoup
from config import IBEX_URL, PRICE_THRESHOLD
from ibex import _HEADERS, _parse_float

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox","--disable-dev-shm-usage"])
        ctx = await browser.new_context(user_agent=_HEADERS["User-Agent"], locale="bg-BG")
        page = await ctx.new_page()
        if HAS_STEALTH:
            await _STEALTH.apply_stealth_async(page)
        await page.goto(IBEX_URL, wait_until="networkidle", timeout=30000)
        await page.wait_for_selector("table", timeout=20000)
        html = await page.content()
        await browser.close()

    soup = BeautifulSoup(html, "lxml")
    all_tables = soup.find_all("table")
    print(f"Found {len(all_tables)} table(s) on page\n")

    for ti, table in enumerate(all_tables):
        rows = table.find_all("tr")
        if not rows:
            continue
        headers = [c.get_text(strip=True) for c in rows[0].find_all(["th","td"])]
        print(f"=== Table {ti+1} — headers: {headers}  ({len(rows)-1} data rows)")

        price_col = next((i for i,h in enumerate(headers) if "Цена" in h or "EUR" in h or "Price" in h), None)
        period_col = next((i for i,h in enumerate(headers) if "Период" in h or "Period" in h or "Hour" in h), None)

        if price_col is None or period_col is None:
            # Just dump all rows raw
            for row in rows[1:6]:
                cells = row.find_all(["td","th"])
                print("  ", [c.get_text(strip=True) for c in cells])
            if len(rows) > 7:
                print(f"  ... ({len(rows)-1} rows total)")
        else:
            print(f"  period_col={period_col}, price_col={price_col}")
            print(f"  {'Period':<20} {'Price (EUR/MWh)':>16}  Action")
            print("  " + "-" * 52)
            for row in rows[1:]:
                cells = row.find_all(["td","th"])
                if len(cells) <= max(price_col, period_col):
                    continue
                period = cells[period_col].get_text(strip=True)
                price = _parse_float(cells[price_col].get_text(strip=True))
                if price is None:
                    continue
                action = "<<< ZERO EXPORT" if price < PRICE_THRESHOLD else ""
                print(f"  {period:<20} {price:>16.2f}  {action}")
        print()

asyncio.run(main())
