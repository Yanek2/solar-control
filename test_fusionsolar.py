"""
Step-by-step FusionSolar diagnostic test.

Runs through each navigation step one at a time, prints every visible
button/link/menu-item found on the page at that point, and saves a
screenshot — so we can see exactly what fails and fix the selectors.

Usage:
    cd C:\\Users\\ykara\\solar-control
    python test_fusionsolar.py

After the test, check the debug/ folder for screenshots of each step.
"""

import asyncio
import logging
import os
import random
import sys
from pathlib import Path

# Force UTF-8 output on Windows so Unicode chars in log messages don't crash
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from playwright.async_api import async_playwright, TimeoutError as PwTimeout

try:
    from playwright_stealth import Stealth
    _STEALTH = Stealth()
except ImportError:
    _STEALTH = None

CREDS_FILE = "creds.txt"
FUSION_SOLAR_URL = "https://eu5.fusionsolar.huawei.com/"
BRAVE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
DEBUG_DIR = Path("debug")
DEBUG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger(__name__)


def load_creds():
    lines = Path(CREDS_FILE).read_text(encoding="utf-8").strip().splitlines()
    return lines[0].strip(), lines[1].strip()


async def delay(lo=700, hi=1600):
    await asyncio.sleep(random.uniform(lo, hi) / 1000)


async def shot(page, name):
    path = DEBUG_DIR / f"{name}.png"
    await page.screenshot(path=str(path))
    log.info("  Screenshot: %s", path)


async def dump_page_elements(page):
    """Print all visible interactive elements — helps identify correct selectors."""
    log.info("  --- Visible clickable elements on this page ---")

    # Nav links and menu items
    items = await page.locator("a, button, [role='menuitem'], [role='tab'], li").all()
    seen = set()
    for el in items[:80]:
        try:
            text = (await el.inner_text()).strip().replace("\n", " ")
            if text and len(text) < 80 and text not in seen:
                seen.add(text)
                log.info("    · %s", text)
        except Exception:
            continue
    log.info("  ---")


async def try_click(page, selectors: list[str], timeout=6000) -> tuple[bool, str]:
    for sel in selectors:
        try:
            await page.wait_for_selector(sel, state="visible", timeout=timeout)
            await page.click(sel)
            return True, sel
        except Exception:
            continue
    return False, ""


async def launch_browser(pw):
    if os.path.exists(BRAVE_PATH):
        try:
            b = await pw.chromium.launch(
                executable_path=BRAVE_PATH,
                headless=False,
                args=["--disable-blink-features=AutomationControlled", "--disable-brave-update"],
            )
            log.info("Browser: Brave")
            return b
        except Exception as e:
            log.warning("Brave failed: %s", e)
    for ch in ("chrome", "msedge"):
        try:
            b = await pw.chromium.launch(channel=ch, headless=False,
                                         args=["--disable-blink-features=AutomationControlled"])
            log.info("Browser: %s", ch)
            return b
        except Exception:
            continue
    log.warning("Falling back to Playwright Chromium")
    return await pw.chromium.launch(headless=False,
                                    args=["--disable-blink-features=AutomationControlled"])


# ============================================================
# TEST STEPS
# ============================================================

async def step_login(page, username, password) -> bool:
    log.info("\n[STEP 1] Login")
    await page.goto(FUSION_SOLAR_URL, wait_until="domcontentloaded", timeout=60_000)
    await delay(1500, 2500)
    await shot(page, "step1_login_page")
    await dump_page_elements(page)

    # FusionSolar login flow (confirmed by user):
    #   1. Type username  →  press Tab  →  focus jumps to the password field
    #   2. Type password  →  press Enter to submit
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
        log.error("  [FAIL] Username field not found — see debug/step1_login_page.png")
        return False

    await page.click(user_sel)
    await delay(200, 400)
    await page.fill(user_sel, "")
    for ch in username:
        await page.type(user_sel, ch, delay=random.randint(50, 130))
    log.info("  Username typed")
    await delay(400, 700)
    await shot(page, "step1b_username_typed")

    # Tab to move focus to the password field
    await page.keyboard.press("Tab")
    await delay(600, 1000)
    await shot(page, "step1c_tabbed_to_password")
    log.info("  Tab pressed — focus should now be on password field")

    # Type password (focus is already on the password field)
    for ch in password:
        await page.keyboard.type(ch, delay=random.randint(50, 130))
    log.info("  Password typed")
    await delay(500, 900)
    await shot(page, "step1d_password_typed")

    # Submit
    await page.keyboard.press("Enter")
    log.info("  Enter pressed to submit — waiting for dashboard …")

    # TargetClosedError here just means the page navigated away — treat as success
    try:
        await page.wait_for_url("**", wait_until="domcontentloaded", timeout=30_000)
    except Exception:
        pass

    await delay(3000, 5000)

    try:
        await shot(page, "step2_after_login")
        url = page.url
    except Exception:
        log.info("  Page context replaced by navigation (normal for FusionSolar)")
        url = "navigated_away"

    if "login" in str(url).lower():
        log.error(
            "  [FAIL] Still on login page — wrong credentials or bot-check.\n"
            "         -> Check debug/step2_after_login.png"
        )
        return False

    log.info("  [OK] Logged in — URL: %s", url)
    return True


async def step_open_plant_tab(context, page):
    """
    Click the house icon on the portal homepage — this opens a NEW TAB
    containing the actual plant/device management interface.
    Returns (new_page, success_bool).
    """
    log.info("\n[STEP 1b] Click house icon to open plant tab")
    await shot(page, "step1e_portal_home")

    # Dump what's on the portal page so we can see the house button
    log.info("  Elements on portal home:")
    await dump_page_elements(page)

    # Listen for a new tab to open
    async with context.expect_page(timeout=20_000) as new_page_info:
        # Try several selectors for the house/home icon
        clicked = False
        for sel in [
            'a[href*="pvms"]',
            '[title*="home" i]',
            '[title*="plant" i]',
            '[class*="home" i] a',
            'img[alt*="home" i]',
            '.station-name a',
            'td a',           # plant name link in a table row
            'a[href*="station"]',
            'a[href*="plant"]',
        ]:
            try:
                await page.wait_for_selector(sel, state="visible", timeout=3000)
                await page.click(sel)
                clicked = True
                log.info("  Clicked house/plant link with: %s", sel)
                break
            except Exception:
                continue

        if not clicked:
            # Try clicking any link that isn't a nav item (likely the plant card)
            try:
                links = await page.locator("a").all()
                for lnk in links:
                    href = await lnk.get_attribute("href") or ""
                    text = (await lnk.inner_text()).strip()
                    log.info("  Available link: '%s' -> %s", text, href[:80])
                    if href and "login" not in href and text:
                        await lnk.click()
                        clicked = True
                        log.info("  Clicked link: %s", href[:80])
                        break
            except Exception as exc:
                log.warning("  Link scan failed: %s", exc)

        if not clicked:
            log.error("  [FAIL] Could not find house/plant icon — see debug/step1e_portal_home.png")
            return page, False

    try:
        new_page = await new_page_info.value
        await new_page.wait_for_load_state("domcontentloaded", timeout=30_000)
        await delay(3000, 5000)
        await shot(new_page, "step1f_plant_tab")
        log.info("  [OK] Plant tab opened — URL: %s", new_page.url)

        # Apply stealth to the new tab too
        if _STEALTH:
            try:
                await _STEALTH.apply_stealth_async(new_page)
            except Exception:
                pass

        return new_page, True
    except Exception as exc:
        log.error("  [FAIL] New tab did not open: %s", exc)
        await shot(page, "step1f_no_new_tab")
        return page, False


async def step_device_management(page) -> bool:
    log.info("\n[STEP 2] Navigate to plant-level Device Management tab")
    log.info("  Plant tab URL: %s", page.url)

    await delay(2000, 3000)  # let plant view fully render

    # The plant view has its own tabs: Overview / Trend / Report Management /
    # Device Management / Alarms / Plant Users.  These are NOT regular <a> links —
    # they are tab buttons that don't change the URL.  The global nav ALSO has a
    # "Device Management" link (href contains /settings/device-management) which
    # takes you to the wrong page, so we must exclude it.

    dm_clicked = False

    # Strategy 1: JS — click the first "Device Management" element whose href
    # does NOT point to the global /settings/ path.
    try:
        result = await page.evaluate("""() => {
            const tags = ['a', 'button', 'li', 'span', 'div'];
            for (const tag of tags) {
                for (const el of document.querySelectorAll(tag)) {
                    const text = (el.innerText || el.textContent || '').trim();
                    if (text !== 'Device Management') continue;
                    const href = el.getAttribute('href') || '';
                    if (href.includes('/settings/')) continue;
                    el.click();
                    return tag + ' | href=' + href;
                }
            }
            return null;
        }""")
        if result:
            log.info("  Clicked plant-level DM tab via JS: %s", result)
            dm_clicked = True
    except Exception as exc:
        log.warning("  JS strategy failed: %s", exc)

    # Strategy 2: Playwright locators — iterate all matches and skip the global one
    if not dm_clicked:
        for sel in ['[role="tab"]:has-text("Device Management")',
                    'li:has-text("Device Management")',
                    'span:has-text("Device Management")',
                    'button:has-text("Device Management")']:
            try:
                locs = await page.locator(sel).all()
                for loc in locs:
                    href = await loc.get_attribute("href") or ""
                    if "/settings/" in href:
                        continue
                    await loc.click(timeout=4000)
                    log.info("  Clicked DM tab with selector: %s", sel)
                    dm_clicked = True
                    break
            except Exception:
                pass
            if dm_clicked:
                break

    if not dm_clicked:
        log.error("  [FAIL] Could not click plant-level Device Management tab")
        await shot(page, "step3_dm_fail")
        return False

    await delay(4000, 6000)
    await shot(page, "step3_device_list")
    log.info("  URL after tab click: %s", page.url)

    # Confirm device list is visible
    try:
        text = await page.inner_text("body", timeout=8000)
        if "Dongle" in text or "Inverter" in text:
            log.info("  [OK] Device list is visible (Dongle / Inverter found in page)")
        else:
            log.warning("  Device list text not detected — check step3_device_list.png")
        for line in text.splitlines():
            line = line.strip()
            if line and any(k in line for k in ["Dongle", "Inverter", "Device name", "SN", "Model"]):
                log.info("    %s", line[:150])
    except Exception:
        pass

    log.info("  [OK] Device Management navigation done")
    return True


async def step_dongle(page) -> bool:
    log.info("\n[STEP 3] Click Dongle-1 to open its detail panel")

    # Clicking the <td> selects the row but does NOT open the detail panel.
    # We need to click the innermost text node / span inside the Device Name cell.
    # Strategy 1: JS — find the leaf element whose text is exactly "Dongle-1"
    clicked = False
    try:
        result = await page.evaluate("""() => {
            const tags = ['a', 'span', 'div', 'td'];
            for (const tag of tags) {
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
            log.info("  JS click on Dongle-1 element: %s", result)
            clicked = True
    except Exception as exc:
        log.warning("  JS click failed: %s", exc)

    # Strategy 2: Playwright locators (more specific first)
    if not clicked:
        for sel in [
            'a:has-text("Dongle-1")',
            'span:has-text("Dongle-1")',
            'td:has-text("Dongle-1") >> a',
            'td:has-text("Dongle-1") >> span',
            'td:has-text("Dongle-1")',
            'text=Dongle-1',
        ]:
            try:
                await page.wait_for_selector(sel, state="visible", timeout=5000)
                await page.click(sel)
                clicked = True
                log.info("  Clicked with selector: %s", sel)
                break
            except Exception:
                continue

    if not clicked:
        log.error("  [FAIL] Cannot click Dongle-1 — check debug/step3_device_list.png")
        await dump_page_elements(page)
        return False

    await delay(2000, 3000)
    await shot(page, "step4_dongle_detail")

    # Verify the detail panel opened — it should show "Basic Information" or
    # tabs like "Configuration", "Real-time Data", etc.
    panel_open = False
    for indicator in ["Basic Information", "Configuration", "Real-time Data", "History Data"]:
        try:
            await page.wait_for_selector(f'text="{indicator}"', timeout=5000)
            log.info("  Detail panel confirmed open (found: '%s')", indicator)
            panel_open = True
            break
        except Exception:
            continue

    if not panel_open:
        log.warning("  Detail panel not detected — trying to open via 'Set Parameters' button")
        # Sometimes you need to select the row first then use Set Parameters
        try:
            await page.click('button:has-text("Set Parameters"), span:has-text("Set Parameters")',
                             timeout=5000)
            await delay(2000, 3000)
            panel_open = True
            log.info("  Opened via Set Parameters")
        except Exception:
            log.warning("  Set Parameters also not available — proceeding anyway")

    log.info("  URL: %s", page.url)
    try:
        text = await page.inner_text("body", timeout=5000)
        for line in text.splitlines():
            line = line.strip()
            if line and any(k in line for k in ["Configuration", "Basic Info", "Active Power", "Parameter"]):
                log.info("    %s", line[:150])
    except Exception:
        pass
    return True


async def step_config_tab(page) -> bool:
    log.info("\n[STEP 4] Click Configuration tab")

    ok, used_sel = await try_click(page, [
        '[role="tab"]:has-text("Configuration")',
        '.tab:has-text("Configuration")',
        'a:has-text("Configuration")',
        'li:has-text("Configuration")',
        'text=Configuration',
    ], timeout=10_000)

    await delay(1500, 2500)
    await shot(page, "step5_config_tab")

    if not ok:
        log.error(
            "  [FAIL] 'Configuration' tab not found.\n"
            "         -> Check debug/step4_dongle_detail.png"
        )
        await dump_page_elements(page)
        return False

    log.info("  [OK] Configuration tab opened (selector: %s)", used_sel)
    return True


async def step_read_apc(page) -> bool:
    """Read the current Active Power Control value (non-destructive)."""
    log.info("\n[STEP 5] Find Active Power Control setting")

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
            log.info("  Found APC element with selector: %s", sel)
            break
        except PwTimeout:
            continue

    await shot(page, "step6_apc_section")

    if not found:
        log.error(
            "  [FAIL] 'Active Power Control' not found on config page.\n"
            "         -> Check debug/step5_config_tab.png"
        )
        # Dump the full visible text so we can see what labels ARE there
        content = await page.inner_text("body")
        log.info("  Page text excerpt:\n%s", content[:3000])
        return False

    # Try to read the current value
    try:
        row = page.locator(
            "tr:has(td:text('Active Power Control')), "
            "div:has(label:text('Active Power Control')), "
            "div:has(span:text('Active Power Control'))"
        ).first
        select_el = row.locator("select").first
        current = await select_el.input_value(timeout=3000)
        opts = [await o.inner_text() for o in await select_el.locator("option").all()]
        log.info("  [OK] Current value: '%s'", current)
        log.info("  Available options: %s", opts)
    except Exception:
        log.info("  (Could not read dropdown value — may be a custom component)")

    return True


async def step_set_zero_export(page) -> bool:
    log.info("\n[STEP 6] Set Active Power Control -> Zero Export Limitation")

    target_labels = [
        "Zero Export to Grid", "Zero Export Limitation", "Zero export",
        "No Power Export to Grid", "Export Limiting",
    ]

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
            log.info("  Option: '%s' (value='%s')", text, val)
            if any(lbl.lower() in text.lower() for lbl in target_labels):
                await select_el.select_option(value=val)
                log.info("  [OK] Selected via <select>: '%s'", text)
                set_ok = True
                break
        if not set_ok:
            log.warning("  No matching <option> — all options logged above")
    except Exception as exc:
        log.warning("  Native <select> failed: %s", exc)

    # Try custom dropdown
    if not set_ok:
        opened = False
        for sel in [
            "div.el-select:near(:text('Active Power Control'))",
            ".ant-select:near(:text('Active Power Control'))",
            "div[class*='select']:near(:text('Active Power Control'))",
        ]:
            try:
                await page.click(sel, timeout=4000)
                opened = True
                await delay(500, 1000)
                log.info("  Dropdown opened with: %s", sel)
                break
            except PwTimeout:
                continue

        if opened:
            for lbl in target_labels:
                for opt_sel in [
                    f'li:has-text("{lbl}")',
                    f'.el-select-dropdown__item:has-text("{lbl}")',
                    f'.ant-select-item:has-text("{lbl}")',
                    f'[class*="option"]:has-text("{lbl}")',
                ]:
                    try:
                        await page.click(opt_sel, timeout=3000)
                        log.info("  [OK] Selected via custom dropdown: '%s'", lbl)
                        set_ok = True
                        break
                    except PwTimeout:
                        continue
                if set_ok:
                    break
        else:
            log.error("  Could not open dropdown — check debug/step6_apc_section.png")

    await shot(page, "step7_mode_set")
    if not set_ok:
        return False

    # Save
    await delay(500, 900)
    ok, used_sel = await try_click(page, [
        'button:has-text("Save")', 'button:has-text("Apply")',
        'button:has-text("Submit")', 'input[type="submit"]',
        '.btn-primary:visible', '[class*="save" i] button:visible',
    ], timeout=8000)

    if not ok:
        log.error("  [FAIL] Save button not found — check debug/step7_mode_set.png")
        await dump_page_elements(page)
        return False

    log.info("  Save clicked (selector: %s)", used_sel)

    # Dismiss confirmation popup if it appears
    await delay(1500, 3000)
    for ok_sel in ['button:has-text("OK")', 'button:has-text("Confirm")',
                   '.el-button--primary:has-text("OK")', '.ant-btn-primary:has-text("OK")',
                   'div[role="dialog"] button:visible']:
        try:
            await page.wait_for_selector(ok_sel, state="visible", timeout=4000)
            await page.click(ok_sel)
            log.info("  Dismissed confirmation popup (%s)", ok_sel)
            break
        except Exception:
            continue

    await delay(2000, 3500)
    await shot(page, "step8_after_save_zero")

    try:
        await page.wait_for_selector(
            ".el-message--success, .ant-message-success, "
            "text=success, text=Success, text=Saved",
            timeout=8000,
        )
        log.info("  [OK] Success message shown -- Zero Export is ACTIVE")
    except Exception:
        log.warning("  No success toast detected -- check debug/step8_after_save_zero.png")

    return True


async def step_set_no_limit(page) -> bool:
    log.info("\n[STEP 7] Set Active Power Control -> No Limitation (restore)")

    target_labels = ["No Limitation", "No Limit", "Unlimited", "Disabled", "None", "Default"]
    set_ok = False

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
            if any(lbl.lower() in text.lower() for lbl in target_labels):
                await select_el.select_option(value=val)
                log.info("  [OK] Selected via <select>: '%s'", text)
                set_ok = True
                break
    except Exception as exc:
        log.warning("  Native <select> failed: %s", exc)

    if not set_ok:
        opened = False
        for sel in [
            "div.el-select:near(:text('Active Power Control'))",
            ".ant-select:near(:text('Active Power Control'))",
            "div[class*='select']:near(:text('Active Power Control'))",
        ]:
            try:
                await page.click(sel, timeout=4000)
                opened = True
                await delay(500, 1000)
                break
            except PwTimeout:
                continue
        if opened:
            for lbl in target_labels:
                for opt_sel in [f'li:has-text("{lbl}")',
                                f'.el-select-dropdown__item:has-text("{lbl}")',
                                f'[class*="option"]:has-text("{lbl}")',]:
                    try:
                        await page.click(opt_sel, timeout=3000)
                        log.info("  [OK] Selected via custom dropdown: '%s'", lbl)
                        set_ok = True
                        break
                    except PwTimeout:
                        continue
                if set_ok:
                    break

    if not set_ok:
        log.error("  [FAIL] Could not select No Limit option")
        return False

    await delay(500, 900)
    ok, _ = await try_click(page, [
        'button:has-text("Save")', 'button:has-text("Apply")',
        'button:has-text("Submit")', 'input[type="submit"]',
        '.btn-primary:visible',
    ], timeout=8000)

    await delay(2000, 3500)
    await shot(page, "step9_after_save_nolimit")

    if ok:
        log.info("  [OK] No Limit saved — solar export restored")
    else:
        log.error("  [FAIL] Save button not found after No Limit selection")

    return ok


# ============================================================
# MAIN
# ============================================================

async def main():
    log.info("=" * 60)
    log.info("FusionSolar Step-by-Step Test")
    log.info("=" * 60)

    username, password = load_creds()

    results = {}

    async with async_playwright() as pw:
        browser = await launch_browser(pw)
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
            timezone_id="Europe/Sofia",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
        )
        page = await context.new_page()
        if _STEALTH:
            await _STEALTH.apply_stealth_async(page)

        results["login"] = await step_login(page, username, password)
        if not results["login"]:
            log.error("\n[ABORTED] Cannot proceed without login.")
        else:
            # The house icon opens a NEW TAB — switch to it before doing anything
            plant_page, results["plant_tab"] = await step_open_plant_tab(context, page)
            if not results["plant_tab"]:
                log.error("\n[ABORTED] Could not open the plant tab.")
            else:
                results["device_mgmt"] = await step_device_management(plant_page)
                if results["device_mgmt"]:
                    results["dongle"] = await step_dongle(plant_page)
                    if results["dongle"]:
                        results["config_tab"] = await step_config_tab(plant_page)
                        if results["config_tab"]:
                            results["read_apc"] = await step_read_apc(plant_page)
                            if results["read_apc"]:
                                results["set_zero"] = await step_set_zero_export(plant_page)
                                if results["set_zero"]:
                                    log.info("\n>>> Pausing 5 seconds — check inverter display <<<")
                                    await asyncio.sleep(5)
                                    results["set_nolimit"] = await step_set_no_limit(plant_page)

        log.info("\n" + "=" * 60)
        log.info("TEST RESULTS:")
        for step, ok in results.items():
            status = "[PASS]" if ok else "[FAIL]"
            log.info("  %s  %s", status, step)
        log.info("=" * 60)

        log.info("\nBrowser will close in 10 seconds (check debug/ folder for screenshots) …")
        await asyncio.sleep(10)
        await browser.close()


if __name__ == "__main__":
    asyncio.run(main())
