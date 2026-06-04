"""
Diagnostic: navigate to Dongle-1 Config, set APC dropdown, then dump all
interactive elements so we can identify the correct Save button selector.
Run on VPS: python3 debug_save.py
"""
import asyncio
import sys
import json
from pathlib import Path

from playwright.async_api import async_playwright, TimeoutError as PwTimeout

try:
    from playwright_stealth import Stealth
    _STEALTH = Stealth()
    HAS_STEALTH = True
except ImportError:
    HAS_STEALTH = False

from config import FUSION_SOLAR_URL, BRAVE_PATH
from ibex import _parse_float  # noqa: F401 (just to verify imports work)

DEBUG_DIR = Path("debug")
DEBUG_DIR.mkdir(exist_ok=True)

CREDS = Path("creds.txt").read_text(encoding="utf-8").strip().splitlines()
USERNAME, PASSWORD = CREDS[0].strip(), CREDS[1].strip()


async def shot(page, name):
    await page.screenshot(path=str(DEBUG_DIR / f"diag_{name}.png"), full_page=True)
    print(f"  [screenshot] debug/diag_{name}.png")


async def main():
    async with async_playwright() as pw:
        args = [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-blink-features=AutomationControlled",
        ]
        browser = await pw.chromium.launch(headless=True, args=args)
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
        page = await context.new_page()
        if HAS_STEALTH:
            await _STEALTH.apply_stealth_async(page)
            print("[stealth applied]")

        # --- Login ---
        print("\n[1] Logging in ...")
        await page.goto(FUSION_SOLAR_URL, wait_until="domcontentloaded", timeout=60000)
        await asyncio.sleep(3)
        await shot(page, "01_login")

        await page.wait_for_selector('input[type="text"]', state="visible", timeout=15000)
        await page.click('input[type="text"]')
        await page.fill('input[type="text"]', "")
        await page.type('input[type="text"]', USERNAME, delay=80)
        await asyncio.sleep(0.5)
        await page.keyboard.press("Tab")
        await asyncio.sleep(0.8)
        await page.keyboard.type(PASSWORD, delay=80)
        await asyncio.sleep(0.6)
        await page.keyboard.press("Enter")
        await asyncio.sleep(5)
        await shot(page, "02_post_login")
        print("    URL:", page.url)

        # --- Open plant tab ---
        print("\n[2] Opening plant tab ...")
        async with context.expect_page(timeout=20000) as new_page_info:
            clicked = False
            for sel in ['a[href*="pvms"]', 'a[href*="station"]', 'td a', '.station-name a']:
                try:
                    await page.wait_for_selector(sel, state="visible", timeout=5000)
                    await page.click(sel)
                    clicked = True
                    break
                except Exception:
                    continue
            if not clicked:
                print("    ERROR: plant link not found")
                await browser.close()
                return
        plant = await new_page_info.value
        await plant.wait_for_load_state("domcontentloaded", timeout=30000)
        await asyncio.sleep(4)
        if HAS_STEALTH:
            try:
                await _STEALTH.apply_stealth_async(plant)
            except Exception:
                pass
        print("    Plant URL:", plant.url)

        # --- Device Management ---
        print("\n[3] Device Management ...")
        await asyncio.sleep(2)
        result = await plant.evaluate("""() => {
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
        print("    DM click result:", result)
        await asyncio.sleep(5)
        await shot(plant, "03_device_list")

        # --- Dongle-1 ---
        print("\n[4] Clicking Dongle-1 ...")
        result = await plant.evaluate("""() => {
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
        print("    Dongle click result:", result)
        await asyncio.sleep(3)
        await shot(plant, "04_dongle_detail")

        # --- Configuration tab ---
        print("\n[5] Configuration tab ...")
        for sel in [
            'a:has-text("Configuration")',
            '[role="tab"]:has-text("Configuration")',
            'text=Configuration',
        ]:
            try:
                await plant.wait_for_selector(sel, state="visible", timeout=8000)
                await plant.click(sel)
                print("    Clicked via:", sel)
                break
            except Exception:
                continue
        await asyncio.sleep(3)
        await shot(plant, "05_config_tab")

        # --- Open APC dropdown ---
        print("\n[6] Opening APC dropdown ...")
        for sel in [
            ".ant-select:near(:text('Active Power Control'))",
            "div.el-select:near(:text('Active Power Control'))",
            "div[class*='select']:near(:text('Active Power Control'))",
        ]:
            try:
                await plant.click(sel, timeout=5000)
                print("    Dropdown opened via:", sel)
                await asyncio.sleep(1)
                break
            except Exception:
                continue

        # Pick any visible option to confirm the dropdown opened
        for opt in ['li:has-text("No Limit")', 'li:has-text("No Limitation")',
                    'li:has-text("Zero")', '.ant-select-item', '.el-select-dropdown__item']:
            try:
                await plant.wait_for_selector(opt, state="visible", timeout=3000)
                await plant.click(opt)
                print("    Option selected via:", opt)
                break
            except Exception:
                continue

        await asyncio.sleep(1)
        await shot(plant, "06_dropdown_set")

        # --- Scroll and dump ALL buttons ---
        print("\n[7] Scrolling to bottom ...")
        await plant.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await asyncio.sleep(1)
        await shot(plant, "07_scrolled_bottom")

        print("\n[8] Dumping all interactive elements ...")
        elements = await plant.evaluate("""() => {
            const results = [];
            const tags = ['button', 'input', 'a', '[role="button"]'];
            const seen = new Set();
            for (const tag of tags) {
                for (const el of document.querySelectorAll(tag)) {
                    const rect = el.getBoundingClientRect();
                    const text = (el.innerText || el.value || el.textContent || '').trim().slice(0, 80);
                    const key = el.tagName + '|' + text + '|' + el.className;
                    if (seen.has(key)) continue;
                    seen.add(key);
                    results.push({
                        tag: el.tagName,
                        text: text,
                        type: el.getAttribute('type') || '',
                        className: el.className.slice(0, 120),
                        id: el.id || '',
                        visible: rect.width > 0 && rect.height > 0,
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        w: Math.round(rect.width),
                        h: Math.round(rect.height),
                        disabled: el.disabled || false,
                    });
                }
            }
            return results;
        }""")

        print(f"\n  Found {len(elements)} interactive elements:")
        for e in elements:
            vis = "VISIBLE" if e["visible"] else "hidden "
            dis = " [DISABLED]" if e["disabled"] else ""
            print(f"    [{vis}] <{e['tag'].lower()}> text={e['text']!r} class={e['className']!r}{dis}")

        # Also dump specifically elements with save/apply/submit text
        print("\n[9] Save-related elements (any visibility):")
        save_els = [e for e in elements if any(
            kw in e["text"].lower() or kw in e["className"].lower()
            for kw in ["save", "apply", "submit", "confirm", "ok"]
        )]
        for e in save_els:
            print(f"    <{e['tag'].lower()}> text={e['text']!r} class={e['className']!r} visible={e['visible']} pos=({e['x']},{e['y']})")

        # Dump full HTML of the form/config area
        print("\n[10] Saving page HTML snippet ...")
        html = await plant.content()
        with open("debug/diag_page.html", "w", encoding="utf-8") as f:
            f.write(html)
        print("    Saved to debug/diag_page.html")

        await browser.close()
        print("\nDone.")


asyncio.run(main())
