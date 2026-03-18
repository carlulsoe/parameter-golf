#!/usr/bin/env bash
set -euo pipefail

HOURS="${1:-6}"
MODEL_A="${2:-gpt-5.4}"
MODEL_B="${3:-gpt-5.3-codex}"
TAG_A="${4:-gpt54}"
TAG_B="${5:-codex53}"
REVIEW_MODEL_A="${REVIEW_MODEL_A:-$MODEL_A}"
REVIEW_MODEL_B="${REVIEW_MODEL_B:-$MODEL_B}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_dir="$(cd "${script_dir}/.." && pwd)"

cd "$repo_dir"
REVIEW_MODEL="$REVIEW_MODEL_A" "${script_dir}/run_pgolf_experiment.sh" "$MODEL_A" "$TAG_A" "$HOURS"
REVIEW_MODEL="$REVIEW_MODEL_B" "${script_dir}/run_pgolf_experiment.sh" "$MODEL_B" "$TAG_B" "$HOURS"
