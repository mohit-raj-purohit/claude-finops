#!/usr/bin/env bash
# Claude FinOps Command Center (macOS / Linux). Same as: python3 run.py [--rebuild|--stop|--share]
cd "$(dirname "$0")" && exec python3 run.py "$@"
