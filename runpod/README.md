# Runpod Worker

This directory contains the worker-side bootstrap path for a disposable GPU pod.

The goal is to make the pod deterministic before the controller starts using it:

1. verify the mounted dataset and tokenizer paths
2. clone or update the repo checkout
3. validate Python, Torch, CUDA, NumPy, and SentencePiece
4. provide a controller-compatible `train_gpt.py` launch path

## Usage

Set the required environment variables:

```bash
export PGOLF_REPO_URL="git@github.com:your-org/parameter-golf.git"
export PGOLF_DATA_ROOT="/mnt/persistent"
export PGOLF_REPO_DIR="/workspace/parameter-golf"
```

Or set explicit paths:

```bash
export DATA_PATH="/mnt/persistent/datasets/fineweb10B_sp1024"
export TOKENIZER_PATH="/mnt/persistent/tokenizers/fineweb_1024_bpe.model"
```

Bootstrap the worker:

```bash
python3 runpod/worker.py bootstrap
```

Run the controller-compatible training command manually:

```bash
RUN_ID=pgolf_test \
DATA_PATH=/mnt/persistent/datasets/fineweb10B_sp1024 \
TOKENIZER_PATH=/mnt/persistent/tokenizers/fineweb_1024_bpe.model \
VOCAB_SIZE=1024 \
VAL_LOSS_EVERY=0 \
ITERATIONS=2500 \
MAX_WALLCLOCK_SECONDS=600 \
python3 runpod/worker.py run
```

## Contract

The script expects:

- `PGOLF_REPO_URL`: git URL for the repo that the controller will push to
- `PGOLF_REPO_DIR`: worker-side checkout, default `/workspace/parameter-golf`
- `PGOLF_BRANCH`: branch name used by the controller, default `runpod-autoresearch`
- `PGOLF_DATA_ROOT` or `DATA_PATH`
- `PGOLF_DATA_ROOT` or `TOKENIZER_PATH`
- `PGOLF_TORCHRUN`: optional, default `torchrun`
- `PGOLF_PYTHON`: optional, default current `python`

If `PGOLF_DATA_ROOT` is set, the script assumes the dataset and tokenizer live at:

- `datasets/fineweb10B_sp1024`
- `tokenizers/fineweb_1024_bpe.model`

The bootstrap command writes a machine-readable report under:

- `PGOLF_STATE_DIR/bootstrap.json`
- `PGOLF_STATE_DIR/bootstrap.env`

