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

    # Find the period price table. Try header keyword matching first,
    # then fall back to any table whose rows start with HH:MM timestamps.
    _PERIOD_KEYWORDS = ("Период", "Period", "Час", "Time", "Интервал")
    _PRICE_KEYWORDS  = ("Цена", "EUR", "Price", "лв", "BGN")

    table = None
    for t in soup.find_all("table"):
        t_rows = t.find_all("tr")
        if not t_rows:
            continue
        t_headers = [c.get_text(strip=True) for c in t_rows[0].find_all(["th", "td"])]
        if any(kw in h for h in t_headers for kw in _PERIOD_KEYWORDS):
            table = t
            logger.debug("Period table found via header keyword; headers: %s", t_headers)
            break

    if not table:
        # Fallback: any table where ≥2 of the first 5 data rows have a cell
        # matching HH:MM (the delivery-period start time).
        time_pat = re.compile(r"^\d{2}:\d{2}")
        for t in soup.find_all("table"):
            data_rows = [r for r in t.find_all("tr") if r.find("td")]
            sample = [r.find("td") for r in data_rows[:5]]
            if sum(1 for c in sample if c and time_pat.match(c.get_text(strip=True))) >= 2:
                table = t
                logger.info("Period table found via time-pattern fallback")
                break

    if not table:
        logger.warning("No period table found on IBEX page")
        return None

    rows = table.find_all("tr")
    if not rows:
        return None

    # Identify column indices from header row (or guess defaults).
    header_cells = rows[0].find_all(["th", "td"])
    headers = [c.get_text(strip=True) for c in header_cells]
    logger.debug("IBEX table headers: %s", headers)

    price_col = next(
        (i for i, h in enumerate(headers) if any(kw in h for kw in _PRICE_KEYWORDS)), 2
    )
    period_col = next(
        (i for i, h in enumerate(headers) if any(kw in h for kw in _PERIOD_KEYWORDS)), 1
    )

    # IBEX table uses CET (UTC+1, no DST) for delivery period times.
    # Sofia in summer (EEST = UTC+3) is 2 h ahead, so 18:01 Sofia = 16:01 CET.
    from datetime import timezone, timedelta
    cet_tz = timezone(timedelta(hours=1))
    now_cet = datetime.now(tz=cet_tz)
    current_hour = now_cet.hour
    quarter_start = (now_cet.minute // 15) * 15
    period_prefix = f"{current_hour:02d}:{quarter_start:02d}"
    hour_prefix   = f"{current_hour:02d}:"   # matches any minute within the hour
    logger.info("Looking for IBEX period '%s' (CET = UTC+1; local Sofia time is UTC+3)", period_prefix)

    fallback_price = None

    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) <= max(price_col, period_col):
            continue

        price_text = cells[price_col].get_text(strip=True)
        price = _parse_float(price_text)
        if price is None:
            continue

        if fallback_price is None:
            fallback_price = price

        period_text = cells[period_col].get_text(strip=True)
        # Match exact 15-min block first, then any entry in the same hour.
        if period_text.startswith(period_prefix):
            logger.info("Price for period '%s': %.2f EUR/MWh", period_text, price)
            return price

    # No exact 15-min match — try matching any row in the current CET hour.
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if len(cells) <= max(price_col, period_col):
            continue
        period_text = cells[period_col].get_text(strip=True)
        if period_text.startswith(hour_prefix):
            price_text = cells[price_col].get_text(strip=True)
            price = _parse_float(price_text)
            if price is not None:
                logger.warning(
                    "No exact quarter match for %s — using hour row '%s': %.2f EUR/MWh",
                    period_prefix, period_text, price,
                )
                return price

    if fallback_price is not None:
        logger.warning(
            "No row matched period %s — returning first available price: %.2f",
            period_prefix, fallback_price,
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
            import asyncio, pathlib
            from datetime import date as _date, timedelta as _td

            await page.goto(IBEX_URL, wait_until="networkidle", timeout=30000)

            # Wait for a <td> whose text starts with HH:MM — that's the price table.
            _WAIT_TIME_CELL = (
                "() => Array.from(document.querySelectorAll('table td')).some("
                "    td => /^\\d{2}:\\d{2}/.test((td.innerText || td.textContent).trim())"
                ")"
            )
            try:
                await page.wait_for_function(_WAIT_TIME_CELL, timeout=15000)
            except Exception:
                await asyncio.sleep(5)

            # Dismiss cookie banner (may not be a <button>; search all elements).
            await page.evaluate("""() => {
                for (const el of document.querySelectorAll('*')) {
                    const t = (el.innerText || el.textContent || '').trim();
                    if (t === 'Ok' || t === 'OK') {
                        const r = el.getBoundingClientRect();
                        if (r.width > 0 && r.height > 0) { el.click(); return; }
                    }
                }
            }""")
            await asyncio.sleep(0.3)

            # IBEX SDAC shows tomorrow's prices by default after ~noon.
            # page.content() doesn't include JS-set input values, so use evaluate().
            shown_date = await page.evaluate(r"""() => {
                for (const inp of document.querySelectorAll('input')) {
                    const v = inp.value.trim();
                    if (/^\d{2}\.\d{2}\.\d{4}$/.test(v)) return v;
                }
                const m = (document.body.innerText || '').match(/\d{2}\.\d{2}\.\d{4}/);
                return m ? m[0] : null;
            }""")
            logger.info("IBEX page shows date: %s", shown_date)

            # Log small elements inside .date-picker-section to find the < button.
            date_area_html = await page.evaluate(r"""() => {
                const section = document.querySelector('.date-picker-section');
                if (!section) return 'no section';
                const inp = section.querySelector('input');
                const inpLeft = inp ? inp.getBoundingClientRect().left : 9999;
                const items = [];
                for (const el of section.querySelectorAll('*')) {
                    const r = el.getBoundingClientRect();
                    if (r.width > 0 && r.height > 0)
                        items.push(el.tagName + '[' + String(el.className||'').slice(0,30) + ']'
                                   + '(x=' + Math.round(r.left) + ',w=' + Math.round(r.width) + ')'
                                   + '=' + (el.innerText||el.textContent||'').trim().slice(0,20));
                }
                return items.join(' | ');
            }""")
            logger.info("Date picker elements: %s", date_area_html)

            tomorrow_str = (_date.today() + _td(days=1)).strftime("%d.%m.%Y")
            if shown_date == tomorrow_str:
                logger.info("IBEX page shows tomorrow (%s) — clicking prev day", tomorrow_str)
                try:
                    await page.click(".date-nav-btn", timeout=3000)
                    logger.info("Clicked .date-nav-btn (prev day)")
                    await page.wait_for_function(_WAIT_TIME_CELL, timeout=10000)
                except Exception as nav_exc:
                    logger.warning("Could not click prev-day button: %s", nav_exc)

            html = await page.content()
            pathlib.Path("debug").mkdir(exist_ok=True)
            await page.screenshot(path="debug/ibex_page.png", full_page=False)
            await browser.close()

        return _extract_price_from_html(html)
    except Exception as exc:
        logger.error("Playwright IBEX fallback failed: %s", exc)
        return None
