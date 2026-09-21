#!/usr/bin/env bash
# Package the app for sharing. Same as: python3 run.py --share
cd "$(dirname "$0")" && exec python3 run.py --share
