"""Quick test — fetches current IBEX price and prints it."""
import asyncio
import logging
from ibex import get_current_price
from config import PRICE_THRESHOLD

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(message)s")

async def main():
    price = await get_current_price()
    if price is None:
        print("\n[FAIL] Could not read IBEX price.")
    else:
        action = "ZERO EXPORT (limit grid export)" if price < PRICE_THRESHOLD else "NO LIMIT (normal)"
        print(f"\n[OK] Current price: {price:.2f} EUR/MWh")
        print(f"     Threshold:      {PRICE_THRESHOLD} EUR/MWh")
        print(f"     Action would be: {action}")

asyncio.run(main())
