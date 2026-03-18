## Local experiment workflow

This file describes the default local workflow for running `parameter-golf` on this machine.

Current local target:
- GPU: `NVIDIA GeForce RTX 3060 Ti (8 GB)`
- Goal: fast local iteration, not leaderboard-competitive training
- Default local preset: `TRAIN_BATCH_TOKENS=139264`

For observed results and batch-size sweep history, see `LOCAL_EXPERIMENTS.md`.

## Principles

- Keep local runs focused on iteration and validation of ideas.
- Do not use local runs to approximate leaderboard timing.
- Use the published tokenizer and a small local dataset slice.
- Keep local changes out of `/records` unless preparing an actual submission snapshot.

## Environment setup

Install and use the repo-local virtual environment:

```bash
/home/linuxbrew/.linuxbrew/bin/python3.13 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

Sanity check CUDA:

```bash
.venv/bin/python - <<'PY'
import torch
print(torch.__version__)
print(torch.cuda.is_available())
print(torch.cuda.get_device_name(0))
PY
```

Expected local outcome:
- CUDA available is `True`
- device name is `NVIDIA GeForce RTX 3060 Ti`

## Dataset setup

Use the smallest published dataset slice that still exercises the real training path:

```bash
.venv/bin/python data/cached_challenge_fineweb.py --variant sp1024 --train-shards 1
```

This should populate:
- `data/datasets/fineweb10B_sp1024/`
- `data/tokenizers/fineweb_1024_bpe.model`

## Default local training preset

Use this as the standard local experiment command:

```bash
RUN_ID=local_default \
ITERATIONS=20 \
WARMUP_STEPS=2 \
TRAIN_BATCH_TOKENS=139264 \
VAL_BATCH_SIZE=8192 \
VAL_LOSS_EVERY=0 \
TRAIN_LOG_EVERY=5 \
MAX_WALLCLOCK_SECONDS=0 \
.venv/bin/torchrun --standalone --nproc_per_node=1 train_gpt.py
```

Why this preset:
- `139264` is the highest confirmed working `TRAIN_BATCH_TOKENS` for the stock compiled script on this GPU.
- `163840` and above failed in the compiled backward path.
- `VAL_BATCH_SIZE=8192` keeps validation safe on `8 GB`.
- `VAL_LOSS_EVERY=0` avoids repeated full-validation passes during short local tests.

## What to expect

- Training starts with a compile-heavy warmup phase.
- Short smoke runs finish training quickly, but final validation is still slow because it evaluates the full validation split in small batches.
- Logs are written to `logs/<RUN_ID>.txt`.
- The trainer also writes `final_model.pt` and `final_model.int8.ptz` in the repo root.

## Recommended local loop

1. Make a small model or optimization change.
2. Run the default local preset above.
3. Inspect `logs/<RUN_ID>.txt` for:
   - successful warmup
   - stable training steps
   - peak memory
   - final `val_bpb`
4. Record any meaningful result in `LOCAL_EXPERIMENTS.md`.

## Current batch-size guidance

- Known good: `65536`
- Known good: `131072`
- Known good: `139264`
- Known bad: `163840`
- Known bad: `196608`
- Known bad: `262144`

The failures above `139264` are not just raw VRAM pressure. On this setup, the stock compiled script also hits a Triton backward-kernel resource limit.
