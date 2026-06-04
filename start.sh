#!/bin/bash
# Start the solar export controller.
# Run from the solar-control directory.

cd "$(dirname "$0")"

if [ ! -f creds.txt ]; then
    echo "ERROR: creds.txt not found."
    echo "Create it with your FusionSolar username on line 1 and password on line 2."
    exit 1
fi

source venv/bin/activate
python main.py
