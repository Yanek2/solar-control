"""Quick test: sets FusionSolar APC to Zero Export Limitation."""
import asyncio
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

from fusion_solar import set_power_control_mode, MODE_ZERO_EXPORT

creds = Path("creds.txt").read_text(encoding="utf-8").strip().splitlines()
username, password = creds[0].strip(), creds[1].strip()

async def main():
    print("\nTesting: set APC -> Zero Export Limitation\n")
    ok = await set_power_control_mode(username, password, MODE_ZERO_EXPORT, retries=1)
    print("\n[RESULT]", "SUCCESS" if ok else "FAILED")

asyncio.run(main())
