#!/usr/bin/env bash
# Publish the Splunk Log Analyzer to its Hugging Face Space. Every upload triggers a rebuild.
# First time only: set the ANTHROPIC_API_KEY secret in the Space's Settings.
set -euo pipefail

SPACE_REPO="rkigobe/splunk-log-analyzer"
FILES=(app.py core.py requirements.txt sample_data/springboot_error_logs.csv)

cd "$(dirname "$0")"

if ! hf auth whoami >/dev/null 2>&1; then
  echo "Not logged in to Hugging Face. Run 'hf auth login' first." >&2
  exit 1
fi

# Creates the Space on the first run; does nothing if it already exists.
hf repo create "$SPACE_REPO" --repo-type space --space_sdk gradio --exist-ok

for f in "${FILES[@]}"; do
  hf upload "$SPACE_REPO" "$f" "$f" --repo-type space --commit-message "Deploy: update $f"
done

# The Space needs its config as YAML frontmatter in README.md, so it gets its
# own README; the repo's README.md is the GitHub one.
hf upload "$SPACE_REPO" HF_SPACE_README.md README.md --repo-type space --commit-message "Deploy: update README.md"

echo "Deployed. Space: https://huggingface.co/spaces/${SPACE_REPO}"
echo "App URL:  https://${SPACE_REPO/\//-}.hf.space"
