"""
Single-cycle solar control check — run by GitHub Actions every 5 minutes.
Reads current mode from docs/status.json, checks IBEX price,
updates FusionSolar if the mode needs to change, writes new status.
"""
import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from config import MAX_RETRIES, PRICE_THRESHOLD
from fusion_solar import MODE_NO_LIMIT, MODE_ZERO_EXPORT, set_power_control_mode
from ibex import get_current_price

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

STATUS_FILE = Path("docs/status.json")


def _read_current_mode() -> str | None:
    if STATUS_FILE.exists():
        try:
            data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
            mode = data.get("mode")
            return mode if mode and mode != "unknown" else None
        except Exception:
            pass
    return None


def _write_status(mode: str | None, price: float | None, error: str | None = None) -> None:
    data = {
        "timestamp": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "mode": mode or "unknown",
        "price": price,
        "threshold": PRICE_THRESHOLD,
        "error": error,
    }
    STATUS_FILE.parent.mkdir(exist_ok=True)
    STATUS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    logger.info("Status written: mode=%s  price=%s  error=%s", mode, price, error)


async def run() -> None:
    username = os.environ.get("FUSION_USER", "").strip()
    password = os.environ.get("FUSION_PASS", "").strip()
    if not username or not password:
        logger.error("FUSION_USER / FUSION_PASS env vars not set")
        sys.exit(1)

    current_mode = _read_current_mode()
    logger.info("Stored mode from last run: %s", current_mode or "unknown")

    price = await get_current_price()

    if price is None:
        logger.warning("Could not read IBEX price — will try again next cycle")
        _write_status(current_mode, None, "Could not read IBEX price")
        return

    logger.info("IBEX price: %.2f EUR/MWh  (threshold: %.2f)", price, PRICE_THRESHOLD)
    desired = MODE_ZERO_EXPORT if price < PRICE_THRESHOLD else MODE_NO_LIMIT

    if desired != current_mode:
        logger.info("Mode change: %s → %s", current_mode or "unknown", desired)
        success = await set_power_control_mode(username, password, desired, retries=MAX_RETRIES)
        if success:
            current_mode = desired
            _write_status(current_mode, price)
        else:
            logger.error("Failed to apply mode '%s'", desired)
            _write_status(current_mode, price, f"Failed to apply mode '{desired}'")
    else:
        logger.info("Mode unchanged: %s", current_mode)
        _write_status(current_mode, price)


if __name__ == "__main__":
    asyncio.run(run())
