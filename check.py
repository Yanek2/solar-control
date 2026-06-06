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
from datetime import datetime, timezone
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
RESYNC_INTERVAL_HOURS = 2


def _read_status() -> tuple[str | None, float | None, datetime | None]:
    """Returns (last_mode, last_price, last_applied_utc) from status.json."""
    if STATUS_FILE.exists():
        try:
            data = json.loads(STATUS_FILE.read_text(encoding="utf-8"))
            mode = data.get("mode")
            mode = mode if mode and mode != "unknown" else None
            price = data.get("price")
            price = float(price) if price is not None else None
            last_applied = None
            ts = data.get("last_applied_utc")
            if ts:
                last_applied = datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
            return mode, price, last_applied
        except Exception:
            pass
    return None, None, None


def _write_status(mode: str | None, price: float | None, error: str | None = None, applied: bool = False, last_applied: datetime | None = None) -> None:
    now = datetime.now(timezone.utc)
    data = {
        "timestamp": now.strftime("%Y-%m-%d %H:%M UTC"),
        "mode": mode or "unknown",
        "price": price,
        "threshold": PRICE_THRESHOLD,
        "error": error,
        "last_applied_utc": (now if applied else last_applied).isoformat() if (applied or last_applied) else None,
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

    current_mode, last_price, last_applied = _read_status()
    logger.info("Stored mode from last run: %s", current_mode or "unknown")

    price = await get_current_price()

    if price is None:
        if last_price is not None:
            price = last_price
            logger.warning(
                "Could not read IBEX price — using last known price: %.2f EUR/MWh",
                price,
            )
        else:
            logger.warning("Could not read IBEX price and no prior price available — skipping")
            _write_status(current_mode, None, "Could not read IBEX price", last_applied=last_applied)
            return

    logger.info("IBEX price: %.2f EUR/MWh  (threshold: %.2f)", price, PRICE_THRESHOLD)
    desired = MODE_ZERO_EXPORT if price < PRICE_THRESHOLD else MODE_NO_LIMIT

    now = datetime.now(timezone.utc)
    hours_since_applied = (
        (now - last_applied).total_seconds() / 3600 if last_applied else float("inf")
    )
    needs_resync = hours_since_applied >= RESYNC_INTERVAL_HOURS

    if desired != current_mode:
        logger.info("Mode change: %s → %s", current_mode or "unknown", desired)
    elif needs_resync:
        logger.info(
            "Periodic re-sync (%.1fh since last FusionSolar call, threshold %dh)",
            hours_since_applied, RESYNC_INTERVAL_HOURS,
        )
    else:
        logger.info("Mode unchanged: %s (%.1fh since last sync)", current_mode, hours_since_applied)
        _write_status(current_mode, price, last_applied=last_applied)
        return

    success = await set_power_control_mode(username, password, desired, retries=MAX_RETRIES)
    if success:
        _write_status(desired, price, applied=True)
    else:
        logger.error("Failed to apply mode '%s'", desired)
        _write_status(current_mode, price, f"Failed to apply mode '{desired}'", last_applied=last_applied)


if __name__ == "__main__":
    asyncio.run(run())
