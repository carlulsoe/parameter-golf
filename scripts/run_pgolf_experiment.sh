#!/usr/bin/env bash
set -euo pipefail

MODEL="${1:-gpt-5.4}"
TAG="${2:-pgolf}"
HOURS="${3:-8}"
REVIEW_MODEL="${REVIEW_MODEL:-$MODEL}"

if ! command -v codex >/dev/null 2>&1; then
  echo "codex CLI not found in PATH" >&2
  exit 1
fi

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "run this script from inside the parameter-golf git repo" >&2
  exit 1
fi

if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "git worktree is dirty; commit or stash before starting the harness" >&2
  exit 1
fi

REPO_DIR="$(pwd)"
export DATA_PATH="${DATA_PATH:-$REPO_DIR/data/datasets/fineweb10B_sp1024}"
export TOKENIZER_PATH="${TOKENIZER_PATH:-$REPO_DIR/data/tokenizers/fineweb_1024_bpe.model}"
export VOCAB_SIZE="${VOCAB_SIZE:-1024}"
export NPROC_PER_NODE="${NPROC_PER_NODE:-1}"
export MAX_WALLCLOCK_SECONDS="${MAX_WALLCLOCK_SECONDS:-600}"
export VAL_LOSS_EVERY="${VAL_LOSS_EVERY:-0}"
export ITERATIONS="${ITERATIONS:-20000}"

RESULTS_FILE="${RESULTS_FILE:-results.tsv}"
HARNESS_LOG="${HARNESS_LOG:-logs/autoresearch_${TAG}.log}"
PROGRAM_FILE="${PROGRAM_FILE:-scripts/pgolf_autoresearch_prompt.md}"
REVIEW_PROGRAM_FILE="${REVIEW_PROGRAM_FILE:-scripts/pgolf_review_prompt.md}"
REVIEWS_FILE="${REVIEWS_FILE:-reviews.tsv}"

mkdir -p logs
touch "$HARNESS_LOG"

if [[ ! -f "$PROGRAM_FILE" ]]; then
  echo "missing prompt file: $PROGRAM_FILE" >&2
  exit 1
fi

if [[ ! -f "$REVIEW_PROGRAM_FILE" ]]; then
  echo "missing review prompt file: $REVIEW_PROGRAM_FILE" >&2
  exit 1
fi

if [[ ! -f "$RESULTS_FILE" ]]; then
  printf "iteration\ttimestamp\tmodel\trun_id\tstatus\tval_bpb\tval_loss\tsize_bytes\tcommit\tidea\tnotes\n" > "$RESULTS_FILE"
fi

if [[ ! -f "$REVIEWS_FILE" ]]; then
  printf "iteration\ttimestamp\tmodel\trun_id\tdecision\tcommit\tsummary\tfindings\n" > "$REVIEWS_FILE"
fi

start_ts="$(date +%s)"
end_ts="$((start_ts + HOURS * 3600))"
iteration=0

while [[ "$(date +%s)" -lt "$end_ts" ]]; do
  iteration="$((iteration + 1))"
  run_id="${TAG}_$(printf '%04d' "$iteration")"
  export RUN_ID="$run_id"

  read -r -d '' PROMPT <<EOF || true
This is Parameter Golf autoresearch iteration ${iteration}.

Repository:
${REPO_DIR}

Model tag:
${MODEL}

Base training command for this iteration:
RUN_ID=${run_id} DATA_PATH=${DATA_PATH} TOKENIZER_PATH=${TOKENIZER_PATH} VOCAB_SIZE=${VOCAB_SIZE} VAL_LOSS_EVERY=${VAL_LOSS_EVERY} ITERATIONS=${ITERATIONS} MAX_WALLCLOCK_SECONDS=${MAX_WALLCLOCK_SECONDS} torchrun --standalone --nproc_per_node=${NPROC_PER_NODE} train_gpt.py

Use this results file:
${RESULTS_FILE}

Follow this protocol file exactly:
${PROGRAM_FILE}

Important:
- Lower final roundtrip val_bpb is better.
- Only run one experiment iteration, then stop.
- Keep the repo clean except for any winning commit and the updated results/log files.
- You may add or change experiment-specific env vars like TRAIN_SEQ_LEN, EVAL_SEQ_LEN, NUM_KV_HEADS, TIE_EMBEDDINGS, MODEL_DIM, NUM_LAYERS, or learning rates for this iteration.
- Keep the dataset path, tokenizer path, entrypoint, and wallclock cap unless the experiment is explicitly about one of those.
- You may use git commit for your candidate change.
- Do not revert the candidate yourself. A fresh reviewer instance will decide keep vs revert.
EOF

  read -r -d '' REVIEW_PROMPT <<EOF || true
This is the review half of Parameter Golf autoresearch iteration ${iteration}.

Repository:
${REPO_DIR}

Experiment model tag:
${MODEL}

Review model tag:
${REVIEW_MODEL}

The experiment run_id for this review is:
${run_id}

Use these files:
- results: ${RESULTS_FILE}
- reviews: ${REVIEWS_FILE}
- review protocol: ${REVIEW_PROGRAM_FILE}

Important:
- Do not run training.
- Review the latest experiment commit and latest matching log for run_id ${run_id}.
- Decide whether to keep or revert the latest experiment commit.
- Update ${REVIEWS_FILE}, and update the latest ${RESULTS_FILE} row notes with your decision summary.
EOF

  {
    echo "===== iteration ${iteration} model=${MODEL} run_id=${run_id} start=$(date -Is) ====="
    codex exec -m "$MODEL" --dangerously-bypass-approvals-and-sandbox "$PROMPT" || true
    echo "===== iteration ${iteration} reviewer=${REVIEW_MODEL} run_id=${run_id} review_start=$(date -Is) ====="
    codex exec -m "$REVIEW_MODEL" --dangerously-bypass-approvals-and-sandbox "$REVIEW_PROMPT" || true
    if ! git diff --quiet || ! git diff --cached --quiet; then
      echo "review left dirty changes for run_id=${run_id}; stopping harness" | tee -a "$HARNESS_LOG"
      exit 1
    fi
    echo "===== iteration ${iteration} model=${MODEL} run_id=${run_id} end=$(date -Is) ====="
  } 2>&1 | tee -a "$HARNESS_LOG"
done

echo "harness finished at $(date -Is)" | tee -a "$HARNESS_LOG"
