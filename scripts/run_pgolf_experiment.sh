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
export REMOTE_HOST="${REMOTE_HOST:-}"
export REMOTE_PORT="${REMOTE_PORT:-22}"
export REMOTE_REPO_DIR="${REMOTE_REPO_DIR:-/workspace/parameter-golf}"
export REMOTE_BRANCH="${REMOTE_BRANCH:-runpod-autoresearch}"
export REMOTE_PYTHON="${REMOTE_PYTHON:-python3}"
export REMOTE_TORCHRUN="${REMOTE_TORCHRUN:-torchrun}"
export REMOTE_IDENTITY="${REMOTE_IDENTITY:-}"

RESULTS_FILE="${RESULTS_FILE:-results.tsv}"
HARNESS_LOG="${HARNESS_LOG:-logs/autoresearch_${TAG}.log}"
PROGRAM_FILE="${PROGRAM_FILE:-scripts/pgolf_autoresearch_prompt.md}"
REVIEW_PROGRAM_FILE="${REVIEW_PROGRAM_FILE:-scripts/pgolf_review_prompt.md}"
REVIEWS_FILE="${REVIEWS_FILE:-reviews.tsv}"
STATE_DIR="${STATE_DIR:-controller_state}"
REMOTE_LOG_DIR="${REMOTE_LOG_DIR:-remote_logs}"

mkdir -p logs
mkdir -p "$STATE_DIR"
mkdir -p "$REMOTE_LOG_DIR"
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
  printf "iteration\ttimestamp\tmodel\treview_model\trun_id\tdecision\tval_bpb\tval_loss\tsize_bytes\tcommit\tidea\tenv\tnotes\n" > "$RESULTS_FILE"
fi

if [[ ! -f "$REVIEWS_FILE" ]]; then
  printf "iteration\ttimestamp\tmodel\trun_id\tdecision\tcommit\tsummary\tfindings\n" > "$REVIEWS_FILE"
fi

start_ts="$(date +%s)"
end_ts="$((start_ts + HOURS * 3600))"
iteration=0

if [[ -z "$REMOTE_HOST" ]]; then
  echo "set REMOTE_HOST=user@host for the Runpod box" >&2
  exit 1
fi

ssh_opts=(-p "$REMOTE_PORT")
if [[ -n "$REMOTE_IDENTITY" ]]; then
  ssh_opts+=(-i "$REMOTE_IDENTITY")
fi
ssh_opts+=(-o StrictHostKeyChecking=accept-new)

sanitize_tsv() {
  printf '%s' "$1" | tr '\t\r\n' '   '
}

latest_kept_bpb() {
  if [[ ! -f "$RESULTS_FILE" ]]; then
    return 0
  fi
  awk -F '\t' 'NR > 1 && $6 == "keep" && $7 != "" { print $7 }' "$RESULTS_FILE" | sort -g | head -n 1
}

while [[ "$(date +%s)" -lt "$end_ts" ]]; do
  iteration="$((iteration + 1))"
  run_id="${TAG}_$(printf '%04d' "$iteration")"
  export RUN_ID="$run_id"
  spec_file="${STATE_DIR}/current_run.env"
  rm -f "$spec_file"

  read -r -d '' PROMPT <<EOF || true
This is Parameter Golf autoresearch experiment-preparation iteration ${iteration}.

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
- Only prepare one experiment iteration, then stop.
- Write the run spec to ${spec_file}.
- Do not run training yourself.
- You may add or change experiment-specific env vars like TRAIN_SEQ_LEN, EVAL_SEQ_LEN, NUM_KV_HEADS, TIE_EMBEDDINGS, MODEL_DIM, NUM_LAYERS, or learning rates for this iteration.
- Keep the dataset path, tokenizer path, entrypoint, and wallclock cap unless the experiment is explicitly about one of those.
- You may use git commit for your candidate change.
- Do not revert the candidate yourself. A fresh reviewer instance will decide keep vs revert.
EOF

  {
    echo "===== iteration ${iteration} model=${MODEL} run_id=${run_id} prep_start=$(date -Is) ====="
    codex exec -m "$MODEL" --dangerously-bypass-approvals-and-sandbox "$PROMPT" || true
    if [[ ! -f "$spec_file" ]]; then
      echo "missing run spec ${spec_file}; stopping harness"
      exit 1
    fi
    if ! git diff --quiet || ! git diff --cached --quiet; then
      echo "prep left dirty changes outside a commit; stopping harness"
      exit 1
    fi

    experiment_commit="$(git rev-parse HEAD)"
    branch_name="$(git branch --show-current)"

    unset IDEA NOTES EXTRA_ENV
    # shellcheck disable=SC1090
    source "$spec_file"
    IDEA="${IDEA:-unspecified}"
    NOTES="${NOTES:-}"
    EXTRA_ENV="${EXTRA_ENV:-}"

    echo "===== iteration ${iteration} model=${MODEL} run_id=${run_id} push_start=$(date -Is) commit=${experiment_commit} branch=${branch_name} ====="
    git push origin "HEAD:refs/heads/${REMOTE_BRANCH}"

    remote_log="${REMOTE_LOG_DIR}/${run_id}.log"
    best_prior_bpb="$(latest_kept_bpb || true)"
    echo "===== iteration ${iteration} model=${MODEL} run_id=${run_id} remote_start=$(date -Is) remote_branch=${REMOTE_BRANCH} ====="
    ssh "${ssh_opts[@]}" "$REMOTE_HOST" "
      set -euo pipefail
      cd '$REMOTE_REPO_DIR'
      git fetch origin '$REMOTE_BRANCH'
      git checkout -B '$REMOTE_BRANCH' FETCH_HEAD
      git reset --hard FETCH_HEAD
      mkdir -p logs
      env RUN_ID='$run_id' DATA_PATH='$DATA_PATH' TOKENIZER_PATH='$TOKENIZER_PATH' VOCAB_SIZE='$VOCAB_SIZE' VAL_LOSS_EVERY='$VAL_LOSS_EVERY' ITERATIONS='$ITERATIONS' MAX_WALLCLOCK_SECONDS='$MAX_WALLCLOCK_SECONDS' ${EXTRA_ENV} '$REMOTE_TORCHRUN' --standalone --nproc_per_node='$NPROC_PER_NODE' train_gpt.py 2>&1 | tee 'logs/${run_id}.log'
    " | tee "$remote_log"

    metrics_line="$(grep 'final_int8_zlib_roundtrip_exact' "$remote_log" | tail -n 1 || true)"
    size_line="$(grep 'Total submission size int8+zlib:' "$remote_log" | tail -n 1 || true)"
    if [[ -z "$metrics_line" ]]; then
      echo "missing final metric line in ${remote_log}; stopping harness"
      exit 1
    fi
    val_loss="$(printf '%s\n' "$metrics_line" | sed -n 's/.*val_loss:\([0-9.]*\).*/\1/p')"
    val_bpb="$(printf '%s\n' "$metrics_line" | sed -n 's/.*val_bpb:\([0-9.]*\).*/\1/p')"
    size_bytes="$(printf '%s\n' "$size_line" | sed -n 's/.*Total submission size int8+zlib: \([0-9]*\) bytes/\1/p')"
    timestamp="$(date -Is)"
    env_summary="$(sanitize_tsv "$EXTRA_ENV")"
    note_summary="$(sanitize_tsv "$NOTES")"
    idea_summary="$(sanitize_tsv "$IDEA")"
    printf "%s\t%s\t%s\t%s\tpending_review\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
      "$iteration" "$timestamp" "$MODEL" "$REVIEW_MODEL" "$run_id" "$val_bpb" "$val_loss" "${size_bytes:-}" "$experiment_commit" "$idea_summary" "$env_summary" "$note_summary" >> "$RESULTS_FILE"

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

The experiment commit for this review is:
${experiment_commit}

The local fetched remote log for this review is:
${remote_log}

The best prior kept val_bpb before this run was:
${best_prior_bpb:-none}

Use these files:
- results: ${RESULTS_FILE}
- reviews: ${REVIEWS_FILE}
- review protocol: ${REVIEW_PROGRAM_FILE}

Important:
- Do not run training.
- Review only the latest experiment commit and the log for run_id ${run_id}.
- Decide whether to keep or revert the latest experiment commit.
- Leave the repository clean when you are done.
EOF

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
