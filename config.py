import sys

IBEX_URL = "https://ibex.bg/sdac-pv-bg/"
FUSION_SOLAR_URL = "https://eu5.fusionsolar.huawei.com/"

PRICE_THRESHOLD = 15.0   # EUR/MWh — below this: zero export, above: no limit
CHECK_INTERVAL = 300     # seconds between IBEX checks (5 min)
CREDS_FILE = "creds.txt"

# Headless=True for VPS/server; False for local debugging (shows browser window)
HEADLESS = sys.platform != "win32"

# Retry attempts if Fusion Solar automation fails
MAX_RETRIES = 3

# Path to Brave browser on Windows. Ignored on Linux (uses Playwright Chromium).
BRAVE_PATH = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
