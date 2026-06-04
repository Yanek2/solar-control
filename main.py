"""
Solar export control — main monitoring loop.

Checks the IBEX SDAC price every CHECK_INTERVAL seconds.
If the price for the current hour drops below PRICE_THRESHOLD:
    → sets FusionSolar Active Power Control to Zero Export Limitation
If it rises back above PRICE_THRESHOLD:
    → sets it back to No Limitation

FusionSolar is only touched when the desired mode actually changes,
to avoid unnecessary logins.
"""

import asyncio
import json
import logging
import os
import sys
import threading
from datetime import datetime
from pathlib import Path

from config import CHECK_INTERVAL, CREDS_FILE, MAX_RETRIES, PRICE_THRESHOLD
from fusion_solar import MODE_NO_LIMIT, MODE_ZERO_EXPORT, set_power_control_mode
from ibex import get_current_price

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

_STATUS_FILE = Path(__file__).parent / "status.json"


def _write_status(mode: str | None, price: float | None, error: str | None = None) -> None:
    data = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "mode": mode or "unknown",
        "price": price,
        "threshold": PRICE_THRESHOLD,
        "error": error,
    }
    try:
        _STATUS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


def load_credentials() -> tuple[str, str]:
    # Prefer environment variables (clean for cloud deployment)
    env_user = os.environ.get("FUSION_USER", "").strip()
    env_pass = os.environ.get("FUSION_PASS", "").strip()
    if env_user and env_pass:
        return env_user, env_pass

    path = Path(CREDS_FILE)
    if not path.exists():
        raise FileNotFoundError(
            f"'{CREDS_FILE}' not found and FUSION_USER/FUSION_PASS env vars not set."
        )
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    if len(lines) < 2:
        raise ValueError("creds.txt must have username on line 1 and password on line 2")
    return lines[0].strip(), lines[1].strip()


def _start_dashboard() -> None:
    try:
        from web_dashboard import start as dashboard_start
        t = threading.Thread(target=dashboard_start, daemon=True)
        t.start()
        logger.info("Dashboard started on http://0.0.0.0:8080/")
    except Exception as exc:
        logger.warning("Dashboard could not start: %s", exc)


async def main() -> None:
    username, password = load_credentials()
    _start_dashboard()

    logger.info("=" * 60)
    logger.info("Solar Export Control started")
    logger.info("  IBEX threshold : %.2f EUR/MWh", PRICE_THRESHOLD)
    logger.info("  Check interval : %ds", CHECK_INTERVAL)
    logger.info("  FusionSolar    : eu5.fusionsolar.huawei.com")
    logger.info("=" * 60)

    current_mode: str | None = None
    _write_status(None, None)  # write immediately so dashboard is never blank

    while True:
        price = await get_current_price()

        if price is None:
            logger.warning("Could not read IBEX price — will retry next cycle")
            _write_status(current_mode, None, "Could not read IBEX price")
        else:
            logger.info("IBEX current-hour price: %.2f EUR/MWh  (threshold: %.2f)", price, PRICE_THRESHOLD)

            desired = MODE_ZERO_EXPORT if price < PRICE_THRESHOLD else MODE_NO_LIMIT

            if desired != current_mode:
                logger.info(
                    "Mode change: %s → %s",
                    current_mode or "unknown",
                    desired,
                )
                success = await set_power_control_mode(
                    username, password, desired, retries=MAX_RETRIES
                )
                if success:
                    current_mode = desired
                    _write_status(current_mode, price)
                else:
                    logger.error(
                        "Failed to apply mode '%s' — will retry next cycle", desired
                    )
                    _write_status(current_mode, price, f"Failed to apply mode '{desired}'")
            else:
                logger.info("Mode unchanged: %s", current_mode)
                _write_status(current_mode, price)

        logger.info("Sleeping %ds until next check …\n", CHECK_INTERVAL)
        await asyncio.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Stopped by user")
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        sys.exit(1)
