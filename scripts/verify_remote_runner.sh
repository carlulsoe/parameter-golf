#!/usr/bin/env bash
set -euo pipefail

REMOTE_HOST="${REMOTE_HOST:-}"
REMOTE_PORT="${REMOTE_PORT:-22}"
REMOTE_REPO_DIR="${REMOTE_REPO_DIR:-/workspace/parameter-golf}"
REMOTE_BRANCH="${REMOTE_BRANCH:-runpod-autoresearch}"
REMOTE_IDENTITY="${REMOTE_IDENTITY:-}"
REMOTE_TORCHRUN="${REMOTE_TORCHRUN:-torchrun}"
DATA_PATH="${DATA_PATH:-./data/datasets/fineweb10B_sp1024}"
TOKENIZER_PATH="${TOKENIZER_PATH:-./data/tokenizers/fineweb_1024_bpe.model}"
VOCAB_SIZE="${VOCAB_SIZE:-1024}"
NPROC_PER_NODE="${NPROC_PER_NODE:-1}"
RUN_ID="${RUN_ID:-remote_verify}"

if [[ -z "$REMOTE_HOST" ]]; then
  echo "set REMOTE_HOST=user@host" >&2
  exit 1
fi

ssh_opts=(-p "$REMOTE_PORT")
if [[ -n "$REMOTE_IDENTITY" ]]; then
  ssh_opts+=(-i "$REMOTE_IDENTITY")
fi
ssh_opts+=(-o StrictHostKeyChecking=accept-new)

echo "Pushing current HEAD to origin/${REMOTE_BRANCH}"
git push origin "HEAD:refs/heads/${REMOTE_BRANCH}"

echo "Running remote smoke test on ${REMOTE_HOST}"
ssh "${ssh_opts[@]}" "$REMOTE_HOST" "
  set -euo pipefail
  cd '$REMOTE_REPO_DIR'
  git fetch origin '$REMOTE_BRANCH'
  git checkout -B '$REMOTE_BRANCH' FETCH_HEAD
  git reset --hard FETCH_HEAD
  mkdir -p logs
  env RUN_ID='$RUN_ID' \
    DATA_PATH='$DATA_PATH' \
    TOKENIZER_PATH='$TOKENIZER_PATH' \
    VOCAB_SIZE='$VOCAB_SIZE' \
    TRAIN_BATCH_TOKENS=8192 \
    VAL_BATCH_SIZE=8192 \
    VAL_LOSS_EVERY=0 \
    ITERATIONS=1 \
    WARMUP_STEPS=0 \
    MAX_WALLCLOCK_SECONDS=30 \
    NUM_LAYERS=2 \
    MODEL_DIM=64 \
    NUM_HEADS=4 \
    NUM_KV_HEADS=2 \
    MLP_MULT=2 \
    TRAIN_SEQ_LEN=256 \
    EVAL_SEQ_LEN=1024 \
    '$REMOTE_TORCHRUN' --standalone --nproc_per_node='$NPROC_PER_NODE' train_gpt.py | tee 'logs/${RUN_ID}.log'
"
