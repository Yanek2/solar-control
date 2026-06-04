import asyncio, random
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PwTimeout

lines = Path('/home/ubuntu/solar-control/creds.txt').read_text().strip().splitlines()
u, p = lines[0].strip(), lines[1].strip()


async def delay(lo=700, hi=1500):
    await asyncio.sleep(random.uniform(lo, hi) / 1000)


async def run():
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(
            headless=True, args=['--no-sandbox', '--disable-dev-shm-usage']
        )
        ctx = await browser.new_context(
            viewport={'width': 1366, 'height': 768},
            user_agent=(
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
                'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'
            ),
            locale='en-US',
        )
        page = await ctx.new_page()

        # Login
        await page.goto('https://eu5.fusionsolar.huawei.com', wait_until='domcontentloaded', timeout=60000)
        await delay(1500, 2500)
        for sel in ['input[name="username"]', 'input[id="username"]', 'input[type="text"]']:
            try:
                await page.wait_for_selector(sel, state='visible', timeout=5000)
                await page.click(sel)
                for ch in u:
                    await page.type(sel, ch, delay=random.randint(50, 120))
                await delay(400, 700)
                await page.keyboard.press('Tab')
                await delay(600, 1000)
                for ch in p:
                    await page.keyboard.type(ch, delay=random.randint(50, 120))
                await delay(500, 900)
                await page.keyboard.press('Enter')
                break
            except PwTimeout:
                continue

        await delay(5000, 7000)
        print('Logged in, URL:', page.url)

        # Click Company Management from nav
        await page.evaluate(
            """() => {
                for (var i = 0; i < document.querySelectorAll('a.threeMenu_title').length; i++) {
                    var el = document.querySelectorAll('a.threeMenu_title')[i];
                    if ((el.innerText || '').trim() === 'Company Management') {
                        el.click();
                        return;
                    }
                }
            }"""
        )
        await delay(5000, 7000)
        await page.screenshot(path='/tmp/fs_company.png')
        print('After Company Mgmt click URL:', page.url)

        # Get all sidebar links (left nav of company page)
        sidebar = await page.evaluate(
            """() => {
                var results = [];
                var els = document.querySelectorAll('a, li, [role="menuitem"], [class*="sidebar"], [class*="left-nav"], [class*="leftNav"]');
                for (var i = 0; i < els.length; i++) {
                    var t = (els[i].innerText || '').trim().split('\\n')[0];
                    if (t && t.length > 2 && t.length < 60) {
                        var href = (els[i].getAttribute('href') || '').slice(0, 100);
                        results.push(els[i].tagName + ' | ' + t + ' | ' + href);
                    }
                }
                var seen = {};
                return results.filter(function(r) {
                    if (seen[r]) return false; seen[r] = true; return true;
                }).slice(0, 50);
            }"""
        )
        print('Company Management nav:')
        for s in sidebar:
            print(' ', s)

        # Also check Personal Settings (dpcloud)
        ps_link = await page.evaluate(
            """() => {
                var els = document.querySelectorAll('a.threeMenu_title');
                for (var i = 0; i < els.length; i++) {
                    if ((els[i].innerText || '').trim() === 'Personal Settings') {
                        return els[i].getAttribute('href');
                    }
                }
                return null;
            }"""
        )
        if ps_link:
            print('\nPersonal Settings href:', ps_link[:120])
            await page.goto('https://eu5.fusionsolar.huawei.com' + ps_link, wait_until='domcontentloaded', timeout=30000)
            await delay(4000, 6000)
            await page.screenshot(path='/tmp/fs_personal.png')
            print('Personal Settings URL:', page.url)

            ps_nav = await page.evaluate(
                """() => {
                    var results = [];
                    var els = document.querySelectorAll('a, li, [role="tab"], [class*="menu-item"]');
                    for (var i = 0; i < els.length; i++) {
                        var t = (els[i].innerText || '').trim().split('\\n')[0];
                        if (t && t.length > 2 && t.length < 60) {
                            var href = (els[i].getAttribute('href') || '').slice(0, 100);
                            results.push(els[i].tagName + ' | ' + t + ' | ' + href);
                        }
                    }
                    var seen = {};
                    return results.filter(function(r) {
                        if (seen[r]) return false; seen[r] = true; return true;
                    }).slice(0, 40);
                }"""
            )
            print('Personal Settings nav:')
            for s in ps_nav:
                print(' ', s)

        await browser.close()


asyncio.run(run())
