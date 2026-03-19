#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_REPO_DIR = Path("/workspace/parameter-golf")
DEFAULT_BRANCH = "runpod-autoresearch"
DEFAULT_DATA_SUBDIR = Path("datasets/fineweb10B_sp1024")
DEFAULT_TOKENIZER_FILE = Path("tokenizers/fineweb_1024_bpe.model")
DEFAULT_STATE_DIRNAME = "runpod_state"
DEFAULT_LOG_DIRNAME = "logs"


class WorkerError(RuntimeError):
    pass


@dataclass(frozen=True)
class WorkerConfig:
    repo_url: str
    repo_dir: Path
    branch: str
    data_path: Path
    tokenizer_path: Path
    python_bin: str
    torchrun_bin: str
    nproc_per_node: int
    state_dir: Path
    log_dir: Path
    run_id: str
    vocab_size: int
    val_loss_every: int
    iterations: int
    max_wallclock_seconds: int


def read_env(name: str, default: str | None = None, *, required: bool = False) -> str:
    value = os.environ.get(name, default)
    if required and (value is None or value == ""):
        raise WorkerError(f"set {name}")
    return value or ""


def run_cmd(args: list[str], *, cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        cwd=cwd,
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def git_output(repo_dir: Path, *args: str) -> str:
    return run_cmd(["git", *args], cwd=repo_dir).stdout.strip()


def ensure_git_repo(repo_dir: Path, repo_url: str) -> None:
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    if not (repo_dir / ".git").exists():
        run_cmd(["git", "clone", repo_url, str(repo_dir)], cwd=repo_dir.parent)
        return
    current_remote = run_cmd(["git", "remote", "get-url", "origin"], cwd=repo_dir, check=False)
    if current_remote.returncode == 0 and current_remote.stdout.strip() != repo_url:
        run_cmd(["git", "remote", "set-url", "origin", repo_url], cwd=repo_dir)
    elif current_remote.returncode != 0:
        run_cmd(["git", "remote", "add", "origin", repo_url], cwd=repo_dir)
    run_cmd(["git", "fetch", "origin", "--prune"], cwd=repo_dir)


def remote_branch_exists(repo_dir: Path, branch: str) -> bool:
    result = run_cmd(["git", "ls-remote", "--heads", "origin", branch], cwd=repo_dir, check=False)
    return result.returncode == 0 and bool(result.stdout.strip())


def origin_default_ref(repo_dir: Path) -> str:
    head_ref = run_cmd(
        ["git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"],
        cwd=repo_dir,
        check=False,
    )
    if head_ref.returncode == 0 and head_ref.stdout.strip():
        return head_ref.stdout.strip()
    for candidate in ("origin/main", "origin/master"):
        result = run_cmd(
            ["git", "rev-parse", "--verify", "--quiet", candidate],
            cwd=repo_dir,
            check=False,
        )
        if result.returncode == 0:
            return candidate
    return "HEAD"


def checkout_branch(repo_dir: Path, branch: str) -> None:
    target_ref = f"origin/{branch}" if remote_branch_exists(repo_dir, branch) else origin_default_ref(repo_dir)
    run_cmd(["git", "checkout", "-B", branch, target_ref], cwd=repo_dir)
    run_cmd(["git", "reset", "--hard", "HEAD"], cwd=repo_dir)


def validate_paths(data_path: Path, tokenizer_path: Path) -> None:
    train_shards = sorted(data_path.glob("fineweb_train_*.bin"))
    val_shards = sorted(data_path.glob("fineweb_val_*.bin"))
    if not data_path.exists():
        raise WorkerError(f"dataset path does not exist: {data_path}")
    if not data_path.is_dir():
        raise WorkerError(f"dataset path is not a directory: {data_path}")
    if not train_shards:
        raise WorkerError(f"dataset path has no train shards: {data_path}")
    if not val_shards:
        raise WorkerError(f"dataset path has no validation shards: {data_path}")
    if not tokenizer_path.exists():
        raise WorkerError(f"tokenizer path does not exist: {tokenizer_path}")
    if not tokenizer_path.is_file():
        raise WorkerError(f"tokenizer path is not a file: {tokenizer_path}")


def validate_python_runtime(python_bin: str) -> dict[str, Any]:
    probe = subprocess.run(
        [
            python_bin,
            "-c",
            (
                "import json, os; "
                "import numpy as np; "
                "import sentencepiece as spm; "
                "import torch; "
                "payload = {"
                "'torch_version': torch.__version__, "
                "'cuda_available': torch.cuda.is_available(), "
                "'cuda_device_count': torch.cuda.device_count(), "
                "'numpy_version': np.__version__, "
                "'sentencepiece_version': getattr(spm, '__version__', 'unknown'), "
                "'cuda_visible_devices': os.environ.get('CUDA_VISIBLE_DEVICES', ''), "
                "}; "
                "payload['cuda_device_name'] = torch.cuda.get_device_name(0) if torch.cuda.is_available() else ''; "
                "print(json.dumps(payload, sort_keys=True))"
            ),
        ],
        check=True,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return json.loads(probe.stdout.strip())


def resolve_config() -> WorkerConfig:
    repo_url = read_env("PGOLF_REPO_URL", required=True)
    repo_dir = Path(read_env("PGOLF_REPO_DIR", str(DEFAULT_REPO_DIR)))
    branch = read_env("PGOLF_BRANCH", DEFAULT_BRANCH)
    python_bin = read_env("PGOLF_PYTHON", sys.executable or "python3")
    torchrun_bin = read_env("PGOLF_TORCHRUN", "torchrun")
    nproc_per_node = int(read_env("PGOLF_NPROC_PER_NODE", "1"))
    run_id = read_env("RUN_ID", "bootstrap")
    vocab_size = int(read_env("VOCAB_SIZE", "1024"))
    val_loss_every = int(read_env("VAL_LOSS_EVERY", "0"))
    iterations = int(read_env("ITERATIONS", "2500"))
    max_wallclock_seconds = int(read_env("MAX_WALLCLOCK_SECONDS", "600"))

    data_path_text = os.environ.get("DATA_PATH")
    tokenizer_path_text = os.environ.get("TOKENIZER_PATH")
    data_root_text = os.environ.get("PGOLF_DATA_ROOT")
    if data_path_text:
        data_path = Path(data_path_text)
    elif data_root_text:
        data_path = Path(data_root_text) / DEFAULT_DATA_SUBDIR
    else:
        raise WorkerError("set DATA_PATH or PGOLF_DATA_ROOT")
    if tokenizer_path_text:
        tokenizer_path = Path(tokenizer_path_text)
    elif data_root_text:
        tokenizer_path = Path(data_root_text) / DEFAULT_TOKENIZER_FILE
    else:
        raise WorkerError("set TOKENIZER_PATH or PGOLF_DATA_ROOT")

    state_dir = Path(read_env("PGOLF_STATE_DIR", str(repo_dir / DEFAULT_STATE_DIRNAME)))
    log_dir = Path(read_env("PGOLF_LOG_DIR", str(repo_dir / DEFAULT_LOG_DIRNAME)))
    return WorkerConfig(
        repo_url=repo_url,
        repo_dir=repo_dir,
        branch=branch,
        data_path=data_path,
        tokenizer_path=tokenizer_path,
        python_bin=python_bin,
        torchrun_bin=torchrun_bin,
        nproc_per_node=nproc_per_node,
        state_dir=state_dir,
        log_dir=log_dir,
        run_id=run_id,
        vocab_size=vocab_size,
        val_loss_every=val_loss_every,
        iterations=iterations,
        max_wallclock_seconds=max_wallclock_seconds,
    )


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_env_file(path: Path, payload: dict[str, Any]) -> None:
    lines = []
    for key in sorted(payload):
        value = payload[key]
        rendered = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
        lines.append(f"{key}={shlex.quote(rendered)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def bootstrap(config: WorkerConfig) -> None:
    ensure_git_repo(config.repo_dir, config.repo_url)
    checkout_branch(config.repo_dir, config.branch)
    validate_paths(config.data_path, config.tokenizer_path)
    runtime = validate_python_runtime(config.python_bin)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.log_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "repo_dir": str(config.repo_dir),
        "repo_url": config.repo_url,
        "branch": config.branch,
        "data_path": str(config.data_path),
        "tokenizer_path": str(config.tokenizer_path),
        "python_bin": config.python_bin,
        "torchrun_bin": config.torchrun_bin,
        "nproc_per_node": config.nproc_per_node,
        "runtime": runtime,
        "run_id": config.run_id,
        "vocab_size": config.vocab_size,
        "val_loss_every": config.val_loss_every,
        "iterations": config.iterations,
        "max_wallclock_seconds": config.max_wallclock_seconds,
    }
    write_json(config.state_dir / "bootstrap.json", report)
    write_env_file(
        config.state_dir / "bootstrap.env",
        {
            "BRANCH": config.branch,
            "DATA_PATH": str(config.data_path),
            "ITERATIONS": str(config.iterations),
            "LOG_DIR": str(config.log_dir),
            "MAX_WALLCLOCK_SECONDS": str(config.max_wallclock_seconds),
            "NPROC_PER_NODE": str(config.nproc_per_node),
            "PYTHON_BIN": config.python_bin,
            "REPO_DIR": str(config.repo_dir),
            "REPO_URL": config.repo_url,
            "RUN_ID": config.run_id,
            "TOKENIZER_PATH": str(config.tokenizer_path),
            "TORCHRUN_BIN": config.torchrun_bin,
            "VAL_LOSS_EVERY": str(config.val_loss_every),
            "VOCAB_SIZE": str(config.vocab_size),
            "CUDA_AVAILABLE": str(runtime["cuda_available"]),
            "CUDA_DEVICE_COUNT": str(runtime["cuda_device_count"]),
            "CUDA_DEVICE_NAME": runtime["cuda_device_name"],
            "NUMPY_VERSION": runtime["numpy_version"],
            "SENTENCEPIECE_VERSION": runtime["sentencepiece_version"],
            "TORCH_VERSION": runtime["torch_version"],
        },
    )
    print(json.dumps(report, indent=2, sort_keys=True))


def run_training(config: WorkerConfig) -> None:
    ensure_git_repo(config.repo_dir, config.repo_url)
    checkout_branch(config.repo_dir, config.branch)
    validate_paths(config.data_path, config.tokenizer_path)
    runtime = validate_python_runtime(config.python_bin)
    config.state_dir.mkdir(parents=True, exist_ok=True)
    config.log_dir.mkdir(parents=True, exist_ok=True)
    write_json(
        config.state_dir / f"{config.run_id}.json",
        {
            "repo_dir": str(config.repo_dir),
            "branch": config.branch,
            "data_path": str(config.data_path),
            "tokenizer_path": str(config.tokenizer_path),
            "runtime": runtime,
            "run_id": config.run_id,
        },
    )
    env = os.environ.copy()
    env.setdefault("RUN_ID", config.run_id)
    env.setdefault("DATA_PATH", str(config.data_path))
    env.setdefault("TOKENIZER_PATH", str(config.tokenizer_path))
    env.setdefault("VOCAB_SIZE", str(config.vocab_size))
    env.setdefault("VAL_LOSS_EVERY", str(config.val_loss_every))
    env.setdefault("ITERATIONS", str(config.iterations))
    env.setdefault("MAX_WALLCLOCK_SECONDS", str(config.max_wallclock_seconds))
    command = [
        config.torchrun_bin,
        "--standalone",
        f"--nproc_per_node={config.nproc_per_node}",
        "train_gpt.py",
    ]
    os.execvpe(config.torchrun_bin, command, env)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Runpod worker bootstrap and training helpers")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("bootstrap", help="verify and prepare the worker")
    subparsers.add_parser("run", help="run the controller-compatible train_gpt.py command")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    config = resolve_config()
    if args.command == "bootstrap":
        bootstrap(config)
        return 0
    if args.command == "run":
        run_training(config)
        return 0
    raise WorkerError(f"unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
