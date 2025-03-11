#!/bin/bash
set -ex

REL_DIR_NAME=$(dirname "$0")
SCRIPT_DIR=$(cd "$REL_DIR_NAME" && pwd -P)
cd "$SCRIPT_DIR"/..

git config --global user.email "interop-scores-bot@users.noreply.github.com"
git config --global user.name "interop-scores-bot"

uv run --project python interop-score --repo-root repos --log-level debug --wpt-fyi https://wpt.fyi/interop-2025 --repo-root repos
cd repos/interop-scores
git status
git log
git push https://x-access-token:${GITHUB_TOKEN}@github.com/jgraham/interop-results.git HEAD:main
