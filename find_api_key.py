import asyncio, random
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PwTimeout

lines = Path('/home/ubuntu/solar-control/creds.txt').read_text().strip().splitlines()
u, p = lines[0].strip(), lines[1].strip()

BASE = ('https://uni002eu5.fusionsolar.huawei.com/uniportal/pvmswebsite/assets/build/'
        'cloud.html?app-id=smartpvms&instance-id=smartpvms&zone-id='
        'region-2-994b6645-6134-4fa7-b223-b4cd403d1d60')


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
        print('Logged in')

        # Navigate to Company Management
        await page.goto(BASE + '#/company/NE=129427379', wait_until='domcontentloaded', timeout=30000)
        await delay(4000, 6000)
        await page.screenshot(path='/tmp/fs_company.png')
        print('Company URL:', page.url)

        # Dump all unique text elements that look like nav/tab items
        links = await page.evaluate(
            """() => {
                var results = [];
                var els = document.querySelectorAll('a, li, [role="tab"], [class*="tab"], [class*="menu-item"], [class*="sidebar"], [class*="nav"]');
                for (var i = 0; i < els.length; i++) {
                    var el = els[i];
                    var t = (el.innerText || '').trim().replace(/\\n.*/,'');
                    if (t && t.length < 50 && t.length > 2) {
                        var href = (el.getAttribute('href') || '').slice(0, 80);
                        results.push(el.tagName + ' | ' + t + ' | ' + href);
                    }
                }
                var seen = {};
                return results.filter(function(r) {
                    if (seen[r]) return false;
                    seen[r] = true;
                    return true;
                }).slice(0, 50);
            }"""
        )
        print('Company page nav items:')
        for l in links:
            print(' ', l)

        # Also check for any Permission/System/NorthBound text anywhere
        found = await page.evaluate(
            """() => {
                var keywords = ['permission', 'system user', 'northbound', 'api', 'third', 'interface'];
                var results = [];
                var all = document.querySelectorAll('*');
                for (var i = 0; i < all.length; i++) {
                    var el = all[i];
                    var t = (el.innerText || '').trim().toLowerCase();
                    if (t.length > 2 && t.length < 80) {
                        for (var k = 0; k < keywords.length; k++) {
                            if (t.indexOf(keywords[k]) !== -1) {
                                results.push(el.tagName + ': ' + t.slice(0,80));
                                break;
                            }
                        }
                    }
                }
                var seen = {};
                return results.filter(function(r) {
                    if (seen[r]) return false;
                    seen[r] = true;
                    return true;
                }).slice(0, 20);
            }"""
        )
        print('API/Permission related text:')
        for f in found:
            print(' ', f)

        await browser.close()


asyncio.run(run())
