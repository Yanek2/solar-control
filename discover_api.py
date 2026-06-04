"""
discover_api.py — Run this ONCE to capture FusionSolar's internal API calls.

What it does:
  1. Opens Chrome/Edge with your credentials (headed, so you can solve any CAPTCHA).
  2. You navigate manually: Device Management → Dongle 1 → Configuration.
  3. You change Active Power Control to "Zero Export" and click Save.
  4. This script records every XHR/fetch call the browser made.
  5. The relevant endpoint + payload is printed and saved to api_calls.json.

Once you have the real endpoint, fusion_solar_api.py can call it directly
with plain requests — no browser, no bot detection.
"""

import asyncio
import json
import logging
from pathlib import Path

from playwright.async_api import async_playwright

try:
    from playwright_stealth import Stealth
    _STEALTH = Stealth()
except ImportError:
    _STEALTH = None

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")
logger = logging.getLogger(__name__)

CREDS_FILE = "creds.txt"
FUSION_SOLAR_URL = "https://eu5.fusionsolar.huawei.com/"
BRAVE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
_CHANNEL_PREFERENCE = ["chrome", "msedge"]
_CAPTURED: list[dict] = []


def _load_creds():
    lines = Path(CREDS_FILE).read_text(encoding="utf-8").strip().splitlines()
    return lines[0].strip(), lines[1].strip()


def _is_api_call(url: str) -> bool:
    skip = {"cdn", "static", ".js", ".css", ".png", ".jpg", ".ico", ".woff", ".svg"}
    return not any(x in url for x in skip)


async def _launch(pw):
    import os
    if os.path.exists(BRAVE_PATH):
        try:
            b = await pw.chromium.launch(
                executable_path=BRAVE_PATH, headless=False,
                args=["--disable-blink-features=AutomationControlled", "--disable-brave-update"],
            )
            logger.info("Browser: Brave")
            return b
        except Exception:
            pass
    for channel in _CHANNEL_PREFERENCE:
        try:
            b = await pw.chromium.launch(channel=channel, headless=False,
                                         args=["--disable-blink-features=AutomationControlled"])
            logger.info("Browser: %s", channel)
            return b
        except Exception:
            continue
    return await pw.chromium.launch(headless=False, args=["--disable-blink-features=AutomationControlled"])


async def main():
    username, password = _load_creds()

    async with async_playwright() as pw:
        browser = await _launch(pw)
        context = await browser.new_context(
            viewport={"width": 1366, "height": 768},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
            locale="en-US",
        )
        await context.add_init_script(
            "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
            "window.chrome={runtime:{}};"
        )
        page = await context.new_page()
        if _STEALTH:
            await _STEALTH.apply_stealth_async(page)

        # Capture ALL requests + responses
        async def on_request(req):
            if _is_api_call(req.url) and req.method in ("POST", "PUT", "PATCH"):
                entry = {"method": req.method, "url": req.url}
                try:
                    entry["post_data"] = req.post_data
                except Exception:
                    pass
                _CAPTURED.append(entry)
                logger.info(">>> %s  %s", req.method, req.url)

        async def on_response(resp):
            if _is_api_call(resp.url) and resp.request.method in ("POST", "PUT", "PATCH"):
                try:
                    body = await resp.json()
                    for entry in reversed(_CAPTURED):
                        if entry["url"] == resp.url:
                            entry["response"] = body
                            break
                except Exception:
                    pass

        page.on("request", on_request)
        page.on("response", on_response)

        # Auto-fill login
        logger.info("Opening FusionSolar …")
        await page.goto(FUSION_SOLAR_URL, wait_until="domcontentloaded", timeout=60_000)
        await asyncio.sleep(2)

        for sel in ['input[name="username"]', 'input[type="text"]']:
            try:
                await page.wait_for_selector(sel, timeout=4000)
                await page.fill(sel, username)
                break
            except Exception:
                continue

        for sel in ['input[name="password"]', 'input[type="password"]']:
            try:
                await page.wait_for_selector(sel, timeout=4000)
                await page.fill(sel, password)
                break
            except Exception:
                continue

        logger.info(
            "\n"
            "=================================================================\n"
            "  The browser is open. If there is a CAPTCHA, solve it now.\n"
            "  Then:\n"
            "    1. Log in (click Login button if not auto-submitted).\n"
            "    2. Navigate: Device Management → Dongle 1 → Configuration.\n"
            "    3. Change Active Power Control → Zero Export → click Save.\n"
            "    4. Then change it back → No Limit → click Save.\n"
            "    5. Come back here and press ENTER to save the captured calls.\n"
            "================================================================="
        )
        input()

        out = Path("api_calls.json")
        out.write_text(json.dumps(_CAPTURED, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("Saved %d API calls to %s", len(_CAPTURED), out)

        # Print any call that looks like a device config write
        logger.info("\n--- Likely config-write calls ---")
        for e in _CAPTURED:
            url = e["url"].lower()
            if any(kw in url for kw in ("param", "config", "setting", "device", "dev")):
                logger.info(json.dumps(e, indent=2, ensure_ascii=False))

        await browser.close()

    logger.info(
        "\nNext step: look at api_calls.json and find the POST that sets Active Power Control.\n"
        "Share it here and I'll build fusion_solar_api.py to call it directly (no browser needed)."
    )


if __name__ == "__main__":
    asyncio.run(main())
