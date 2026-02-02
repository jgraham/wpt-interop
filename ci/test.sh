#!/bin/bash
set -ex

REL_DIR_NAME=$(dirname "$0")
SCRIPT_DIR=$(cd "$REL_DIR_NAME" && pwd -P)
cd "$SCRIPT_DIR"/..

cargo test

cd python
uv sync --extra=test
uv run ty check python/wpt_interop/
uv run ruff check
uv run ruff format --check
