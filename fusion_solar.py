"""
Controls Active Power Control on FusionSolar EU5.

Confirmed navigation flow (tested 2026-05-14):
  Login -> portal home -> house icon (new tab) -> Device Management tab
  -> click Dongle-1 (JS inner-element click) -> Configuration tab
  -> Active Power Control ant-select dropdown -> Save

Bot-detection strategy:
  1. Real Brave browser (not Playwright Chromium).
  2. Headed window.
  3. playwright-stealth patches JS fingerprints.
  4. Human-like random delays between actions.
"""

import asyncio
import logging
import random
from pathlib import Path

from playwright.async_api import BrowserContext, Page, async_playwright, TimeoutError as PwTimeout

try:
    from playwright_stealth import Stealth
    _STEALTH = Stealth()
    _HAS_STEALTH = True
except ImportError:
    _HAS_STEALTH = False

from config import BRAVE_PATH, FUSION_SOLAR_URL, HEADLESS

logger = logging.getLogger(__name__)

DEBUG_DIR = Path("debug")
DEBUG_DIR.mkdir(exist_ok=True)

MODE_ZERO_EXPORT = "zero_export"
MODE_NO_LIMIT = "no_limit"

# Reads the current Active Power Control dropdown value.
# Walks up from the label element but only searches SIBLING subtrees at each
# level, so it never accidentally returns a device-type dropdown higher on the
# page (e.g. 'WIFI_DONGLE') that lives in a shared ancestor.
_READ_APC_JS = """() => {
    const label = 'Active Power Control';
    for (const tag of ['td', 'span', 'div', 'label', 'th']) {
        for (const el of document.querySelectorAll(tag)) {
            if ((el.innerText || el.textContent || '').trim() !== label) continue;
            let child = el;
            let parent = el.parentElement;
            for (let i = 0; i < 6; i++) {
                if (!parent) break;
                for (const sib of Array.from(parent.children)) {
                    if (sib === child) continue;
                    for (const s of ['.ant-select-selection-item',
                                     '.ant-select-selection__rendered',
                                     '.el-select .el-input__inner']) {
                        const d = sib.querySelector(s);
                        if (d) { const v = (d.innerText || d.value || '').trim(); if (v) return v; }
                    }
                }
                child = parent;
                parent = parent.parentElement;
            }
        }
    }
    return '';
}"""

_MODE_LABELS = {
    MODE_ZERO_EXPORT: [
        "Zero Export to Grid",
        "Zero Export Limitation",
        "Zero export",
        "No Power Export to Grid",
        "Export Limiting",
        "Zero Power Output",
    ],
    MODE_NO_LIMIT: [
        "No Limitation",
        "No Limit",
        "Unlimited",
        "Disabled",
        "None",
        "Default",
        "Normal",
    ],
}


async def _delay(lo: int = 700, hi: int = 1800) -> None:
    await asyncio.sleep(random.uniform(lo, hi) / 1000)


async def _shot(page: Page, name: str) -> None:
    try:
        await page.screenshot(path=str(DEBUG_DIR / f"{name}.png"))
    except Exception:
        pass


async def _click(page: Page, selectors: list[str], timeout: int = 6000) -> bool:
    for sel in selectors:
        try:
            await page.wait_for_selector(sel, state="visible", timeout=timeout)
            await page.click(sel)
            return True
        except Exception:
            continue
    return False


# ---------------------------------------------------------------------------
# Browser launch
# ---------------------------------------------------------------------------

async def _launch(pw):
    import os, sys

    # Extra args needed on headless Linux VPS (no X server, often running as root)
    linux_args = ["--no-sandbox", "--disable-dev-shm-usage"] if sys.platform != "win32" else []
    base_args = ["--disable-blink-features=AutomationControlled"] + linux_args

    # Windows: try Brave first
    if sys.platform == "win32" and os.path.exists(BRAVE_PATH):
        try:
            browser = await pw.chromium.launch(
                executable_path=BRAVE_PATH,
                headless=HEADLESS,
                args=base_args + ["--disable-brave-update"],
            )
            logger.info("Using Brave: %s", BRAVE_PATH)
            return browser
        except Exception as exc:
            logger.warning("Brave launch failed (%s), trying next ...", exc)

    for channel in ("chrome", "msedge"):
        try:
            browser = await pw.chromium.launch(
                channel=channel,
                headless=HEADLESS,
                args=base_args,
            )
            logger.info("Using browser channel: %s", channel)
            return browser
        except Exception:
            continue

    logger.warning("Using Playwright Chromium (bot detection possible on first run)")
    return await pw.chromium.launch(headless=HEADLESS, args=base_args)


async def _make_page(browser) -> tuple:
    """Returns (context, page)."""
    context = await browser.new_context(
        viewport={"width": 1366, "height": 768},
        user_agent=(
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        locale="en-US",
        timezone_id="Europe/Sofia",
    )
    await context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['en-US','en'] });
        window.chrome = { runtime: {} };
        delete window.__playwright;
        delete window.__pw_manual;
    """)
    page = await context.new_page()
    if _HAS_STEALTH:
        await _STEALTH.apply_stealth_async(page)
        logger.debug("playwright-stealth applied")
    return context, page


# ---------------------------------------------------------------------------
# Step 1 -- Login
# ---------------------------------------------------------------------------

async def _login(page: Page, username: str, password: str) -> bool:
    logger.info("Opening FusionSolar ...")
    await page.goto(FUSION_SOLAR_URL, wait_until="domcontentloaded", timeout=60_000)
    await _delay(1500, 2500)
    await _shot(page, "01_login_page")

    user_sel = None
    for sel in ['input[name="username"]', 'input[id="username"]',
                '#loginForm input[type="text"]', 'input[type="text"]']:
        try:
            await page.wait_for_selector(sel, state="visible", timeout=5000)
            user_sel = sel
            break
        except PwTimeout:
            continue

    if not user_sel:
        logger.error("Username field not found -- see debug/01_login_page.png")
        return False

    # FusionSolar login: username -> Tab -> password -> Enter
    await page.click(user_sel)
    await _delay(200, 400)
    await page.fill(user_sel, "")
    for ch in username:
        await page.type(user_sel, ch, delay=random.randint(50, 120))
    await _delay(400, 700)

    await page.keyboard.press("Tab")
    await _delay(600, 1000)

    for ch in password:
        await page.keyboard.type(ch, delay=random.randint(50, 120))
    await _delay(500, 900)
    await page.keyboard.press("Enter")
    logger.info("Credentials submitted -- waiting for dashboard ...")

    try:
        await page.wait_for_url("**", wait_until="domcontentloaded", timeout=30_000)
    except Exception:
        pass

    await _delay(3000, 5000)

    try:
        await _shot(page, "02_post_login")
        url = page.url
    except Exception:
        url = "navigated_away"

    if "login" in str(url).lower():
        logger.error(
            "Still on login page -- wrong credentials or bot check triggered. "
            "See debug/02_post_login.png"
        )
        return False

    logger.info("Logged in. URL: %s", page.url)
    return True


# ---------------------------------------------------------------------------
# Step 2 -- Open plant tab (house icon opens a new tab)
# ---------------------------------------------------------------------------

async def _open_plant_tab(context: BrowserContext, page: Page) -> Page | None:
    """Click the plant/house link on the portal home; return the new tab."""
    logger.info("Opening plant tab ...")
    try:
        async with context.expect_page(timeout=20_000) as new_page_info:
            clicked = await _click(page, [
                'a[href*="pvms"]',
                '[title*="home" i]',
                '[title*="plant" i]',
                'a[href*="station"]',
                'a[href*="plant"]',
                '.station-name a',
                'td a',
            ], timeout=5000)
            if not clicked:
                logger.error("Could not find plant/house link -- see debug/02_post_login.png")
                return None

        new_page = await new_page_info.value
        await new_page.wait_for_load_state("domcontentloaded", timeout=30_000)
        await _delay(3000, 5000)
        if _HAS_STEALTH:
            try:
                await _STEALTH.apply_stealth_async(new_page)
            except Exception:
                pass
        logger.info("Plant tab opened -- URL: %s", new_page.url)
        return new_page
    except Exception as exc:
        logger.error("Plant tab did not open: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Step 3 -- Plant-level Device Management tab
# ---------------------------------------------------------------------------

async def _open_device_management(page: Page) -> bool:
    """Click the Device Management tab within the plant view (NOT the global nav link)."""
    logger.info("Navigating to plant-level Device Management ...")
    await _delay(2000, 3000)

    # JS: click the first "Device Management" element whose href does NOT go to /settings/
    try:
        result = await page.evaluate("""() => {
            for (const tag of ['a', 'button', 'li', 'span', 'div']) {
                for (const el of document.querySelectorAll(tag)) {
                    const text = (el.innerText || el.textContent || '').trim();
                    if (text !== 'Device Management') continue;
                    const href = el.getAttribute('href') || '';
                    if (href.includes('/settings/')) continue;
                    el.click();
                    return tag + ':' + href;
                }
            }
            return null;
        }""")
        if result:
            logger.info("Plant-level Device Management clicked: %s", result)
            await _delay(4000, 6000)
            await _shot(page, "03_device_list")
            return True
    except Exception as exc:
        logger.warning("JS Device Management click failed: %s", exc)

    logger.error("Could not navigate to plant-level Device Management")
    return False


# ---------------------------------------------------------------------------
# Step 4 -- Click Dongle-1 (opens detail panel)
# ---------------------------------------------------------------------------

async def _open_dongle(page: Page) -> bool:
    """Click the Dongle-1 name to open its detail panel."""
    logger.info("Clicking Dongle-1 ...")

    # JS: find the leaf element with exact text "Dongle-1" and click it.
    # This opens the right-side detail panel (not just row selection).
    try:
        result = await page.evaluate("""() => {
            for (const tag of ['a', 'span', 'div', 'td']) {
                for (const el of document.querySelectorAll(tag)) {
                    const text = (el.innerText || '').trim();
                    if (text === 'Dongle-1' || text === 'Dongle 1') {
                        el.click();
                        return tag + ':' + el.className;
                    }
                }
            }
            return null;
        }""")
        if result:
            logger.info("Dongle-1 clicked: %s", result)
    except Exception as exc:
        logger.warning("JS dongle click failed: %s -- trying CSS selectors", exc)
        ok = await _click(page, [
            'a:has-text("Dongle-1")',
            'span:has-text("Dongle-1")',
            'td:has-text("Dongle-1") >> a',
            'td:has-text("Dongle-1")',
        ], timeout=10_000)
        if not ok:
            logger.error("Dongle-1 not found -- see debug/03_device_list.png")
            return False

    await _delay(2000, 3000)
    await _shot(page, "04_dongle_detail")

    # Verify detail panel opened
    for indicator in ["Basic Information", "Configuration", "Real-time Data"]:
        try:
            await page.wait_for_selector(f'text="{indicator}"', timeout=5000)
            logger.info("Dongle detail panel confirmed open ('%s' visible)", indicator)
            return True
        except Exception:
            continue

    logger.warning("Dongle detail panel not confirmed -- proceeding anyway")
    return True


# ---------------------------------------------------------------------------
# Step 5 -- Configuration tab
# ---------------------------------------------------------------------------

async def _open_config_tab(page: Page) -> bool:
    ok = await _click(page, [
        'a:has-text("Configuration")',
        '[role="tab"]:has-text("Configuration")',
        'li:has-text("Configuration")',
        'text=Configuration',
    ], timeout=15_000)

    if not ok:
        logger.error("'Configuration' tab not found -- see debug/04_dongle_detail.png")
        return False

    await _delay(1500, 2500)
    await _shot(page, "05_config_tab")
    return True


# ---------------------------------------------------------------------------
# Step 6 -- Set Active Power Control
# ---------------------------------------------------------------------------

async def _set_apc(page: Page, mode: str) -> bool:
    labels = _MODE_LABELS[mode]
    logger.info("Target mode labels: %s", labels)

    # Scroll to APC section
    found = False
    for sel in [
        'text=Active Power Control',
        'label:has-text("Active Power Control")',
        'td:has-text("Active Power Control")',
        'span:has-text("Active Power Control")',
    ]:
        try:
            el = page.locator(sel).first
            await el.scroll_into_view_if_needed(timeout=8000)
            found = True
            break
        except Exception:
            continue

    if not found:
        logger.error("'Active Power Control' not found -- see debug/05_config_tab.png")
        await _shot(page, "06_apc_notfound")
        return False

    await _shot(page, "06_apc_found")
    await _delay(500, 1000)

    # Read current value before touching the dropdown
    current_val = await page.evaluate(_READ_APC_JS)
    logger.info("Current APC value: '%s'", current_val)

    already_set = current_val and any(
        lbl.lower() in current_val.lower() or current_val.lower() in lbl.lower()
        for lbl in labels
    )
    if already_set:
        logger.info("APC already set to '%s' -- no change needed, skipping save", current_val)
        return True

    set_ok = False

    # Try native <select>
    try:
        row = page.locator(
            "tr:has(td:text('Active Power Control')), "
            "div:has(label:text('Active Power Control')), "
            "div:has(span:text('Active Power Control'))"
        ).first
        select_el = row.locator("select").first
        opts = await select_el.locator("option").all()
        for opt in opts:
            text = (await opt.inner_text()).strip()
            val = await opt.get_attribute("value")
            if any(lbl.lower() in text.lower() for lbl in labels):
                await select_el.select_option(value=val)
                logger.info("Set via <select>: '%s'", text)
                set_ok = True
                break
        if not set_ok:
            raise ValueError("No matching option")
    except Exception as exc:
        logger.debug("Native <select> strategy failed: %s", exc)

    # Try ant-select / element-ui custom dropdown (confirmed working)
    if not set_ok:
        opened = False
        for sel in [
            ".ant-select:near(:text('Active Power Control'))",
            "div.el-select:near(:text('Active Power Control'))",
            "div[class*='select']:near(:text('Active Power Control'))",
            "div[class*='dropdown']:near(:text('Active Power Control'))",
        ]:
            try:
                await page.click(sel, timeout=4000)
                opened = True
                await _delay(500, 1000)
                break
            except Exception:
                continue

        if not opened:
            logger.error("Cannot open APC dropdown -- see debug/06_apc_found.png")
            await _shot(page, "06_dropdown_fail")
            return False

        for lbl in labels:
            for opt_sel in [
                f'li:has-text("{lbl}")',
                f'.el-select-dropdown__item:has-text("{lbl}")',
                f'.ant-select-item:has-text("{lbl}")',
                f'[class*="option"]:has-text("{lbl}")',
            ]:
                try:
                    await page.click(opt_sel, timeout=3000)
                    logger.info("Set via custom dropdown: '%s'", lbl)
                    set_ok = True
                    break
                except Exception:
                    continue
            if set_ok:
                break

    if not set_ok:
        logger.error("Could not set dropdown -- see debug/06_apc_found.png")
        await _shot(page, "06_set_fail")
        return False

    # Close dropdown if still open (press Escape)
    try:
        await page.keyboard.press("Escape")
        await _delay(300, 500)
    except Exception:
        pass

    await _shot(page, "07_mode_selected")
    # Wait for UI to react to dropdown change
    await _delay(2000, 3000)

    # Dismiss cookies/consent banner so it doesn't block the Save button
    for ck_sel in [
        'button:has-text("Accept")',
        'button:has-text("OK")',
        'button:has-text("Close")',
        '[aria-label="close"]',
        '.cookie-banner button',
        '#cookie-accept',
        'button[class*="cookie" i]',
        '.cc-btn.cc-allow',
    ]:
        try:
            await page.click(ck_sel, timeout=2000)
            logger.debug("Dismissed cookie banner via: %s", ck_sel)
            await _delay(300, 500)
            break
        except Exception:
            continue

    saved = False

    # JS save finder — includes disabled buttons (Save can be "disabled" when form
    # uses its own dirty-state tracking but is still physically clickable)
    try:
        js_result = await page.evaluate("""() => {
            const keywords = ['save', 'apply', 'submit'];
            const els = Array.from(document.querySelectorAll(
                'button, input[type="submit"], input[type="button"], [role="button"]'
            ));
            // visible = has layout dimensions (ignore display:none / visibility:hidden)
            const visible = els.filter(el => {
                const r = el.getBoundingClientRect();
                return r.width > 0 && r.height > 0;
            });
            // keyword match (case-insensitive on text)
            const match = visible.find(el => {
                const t = (el.innerText || el.value || el.textContent || '').trim().toLowerCase();
                return keywords.some(k => t.includes(k));
            });
            if (match) {
                const t = (match.innerText || match.value || match.textContent || '').trim();
                match.click();
                return 'clicked:' + t + ':' + match.className + ':disabled=' + match.disabled;
            }
            const report = visible.map(el => {
                const t = (el.innerText || el.value || el.textContent || '').trim().slice(0, 40);
                return t + '/' + el.tagName + '/' + el.className.slice(0, 60) + '/d=' + el.disabled;
            }).join(' | ');
            return 'notfound:' + report;
        }""")

        if js_result and js_result.startswith("clicked:"):
            parts = js_result.split(":", 3)
            logger.info("Save clicked via JS: text='%s' disabled=%s", parts[1], parts[3] if len(parts) > 3 else "?")
            saved = True
        else:
            logger.warning("JS save scan: %s", js_result)
    except Exception as exc:
        logger.warning("JS save finder failed: %s", exc)

    # CSS fallback
    if not saved:
        saved = await _click(page, [
            'button:has-text("Save")',
            'button:has-text("Submit")',
            'button:has-text("Apply")',
            'input[value="Save"]',
            'input[type="submit"]',
        ], timeout=8000)
        if saved:
            logger.info("Save clicked via CSS selector")

    if not saved:
        await _shot(page, "07b_save_not_found")
        logger.error("Save button not found -- see debug/07b_save_not_found.png")
        return False

    # FusionSolar shows a confirmation popup after Save — dismiss it.
    await _delay(1500, 3000)
    for ok_sel in [
        'button:has-text("OK")',
        'button:has-text("Ok")',
        'button:has-text("Confirm")',
        '.el-button--primary:has-text("OK")',
        '.ant-btn-primary:has-text("OK")',
        '.modal-footer button:visible',
        'div[role="dialog"] button:visible',
    ]:
        try:
            await page.wait_for_selector(ok_sel, state="visible", timeout=4000)
            await page.click(ok_sel)
            logger.info("Dismissed confirmation popup")
            break
        except Exception:
            continue

    await _delay(2000, 3500)
    await _shot(page, "08_after_save")

    toast_seen = False
    try:
        await page.wait_for_selector(
            ".el-message--success, .ant-message-success, "
            "text=success, text=Success, text=Saved",
            timeout=8000,
        )
        logger.info("Save confirmed by success toast")
        toast_seen = True
    except Exception:
        logger.warning("No success toast -- verifying by reading back dropdown value")

    # Read back the APC dropdown specifically to confirm the change actually stuck.
    await _delay(1500, 2500)
    verified_val = await page.evaluate(_READ_APC_JS)
    logger.info("Post-save APC value: '%s'", verified_val)

    confirmed = verified_val and any(
        lbl.lower() in verified_val.lower() or verified_val.lower() in lbl.lower()
        for lbl in labels
    )
    if confirmed:
        logger.info("Save verified: APC is now '%s'", verified_val)
        return True

    if toast_seen:
        logger.warning("Toast seen but read-back shows '%s' -- proceeding", verified_val)
        return True

    logger.error(
        "Save failed: APC still reads '%s' after save attempt (check debug/08_after_save.png)",
        verified_val,
    )
    return False


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def set_power_control_mode(
    username: str,
    password: str,
    mode: str,
    *,
    retries: int = 3,
) -> bool:
    if mode not in (MODE_ZERO_EXPORT, MODE_NO_LIMIT):
        raise ValueError(f"Unknown mode: {mode!r}")

    for attempt in range(1, retries + 1):
        logger.info("Attempt %d/%d -- mode=%s", attempt, retries, mode)
        try:
            async with async_playwright() as pw:
                browser = await _launch(pw)
                context, page = await _make_page(browser)

                if not await _login(page, username, password):
                    await browser.close()
                    continue

                plant_page = await _open_plant_tab(context, page)
                if plant_page is None:
                    await browser.close()
                    continue

                ok = (
                    await _open_device_management(plant_page)
                    and await _open_dongle(plant_page)
                    and await _open_config_tab(plant_page)
                    and await _set_apc(plant_page, mode)
                )
                await browser.close()

            if ok:
                logger.info("Mode applied: %s", mode)
                return True
        except Exception as exc:
            logger.error("Attempt %d exception: %s", attempt, exc)

        if attempt < retries:
            wait = 30 * attempt
            logger.info("Waiting %ds before retry ...", wait)
            await asyncio.sleep(wait)

    return False
