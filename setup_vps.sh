#!/bin/bash
# Run this ONCE on the VPS to install everything.
# Usage:  bash setup_vps.sh

set -e

echo "=== Installing system dependencies ==="
apt-get update -y
apt-get install -y python3 python3-pip python3-venv

echo "=== Creating Python virtual environment ==="
python3 -m venv venv
source venv/bin/activate

echo "=== Installing Python packages ==="
pip install --upgrade pip
pip install playwright playwright-stealth requests beautifulsoup4 lxml

echo "=== Installing Playwright Chromium + its system dependencies ==="
playwright install chromium
playwright install-deps chromium

echo ""
echo "=== Setup complete! ==="
echo ""
echo "Next steps:"
echo "  1. Make sure creds.txt exists (username on line 1, password on line 2)"
echo "  2. Start the service:  bash start.sh"
echo "  3. Or install as systemd service (see solar-control.service)"
