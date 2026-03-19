# Hetzner Controller Deployment

This directory contains the first-pass automation for deploying the autoresearch
controller onto a long-lived controller host such as Hetzner.

The deployment model is:

- Hetzner runs the controller, Codex CLI, trace storage, and queue.
- Runpod runs the disposable GPU worker that the controller SSHes into.

## Prerequisites

On the controller host:

- `python3`
- `git`
- `systemd --user`
- `codex` installed and already authenticated

The deploy script will install `uv` automatically if it is missing.

## Runtime Contract

Prepare an env file from
[autoresearch.env.example](/var/home/carlulsoechristensen/Documents/parameter-golf/infra/hetzner/autoresearch.env.example).

That file defines:

- the remote Runpod worker SSH target
- the dataset/tokenizer paths visible on the GPU worker
- the default experiment budget
- the controller trace and ledger locations

## Deploy

Run from the repository root, after committing your deployment target state:

```bash
python3 infra/hetzner/deploy_controller.py \
  --host your-user@your-hetzner-host \
  --env-file /absolute/path/to/autoresearch.env \
  --start
```

Useful flags:

- `--identity ~/.ssh/hetzner_ed25519`
- `--port 2222`
- `--controller-args "--hours 8"`
- `--enable-linger`
- `--dry-run`

## Notes

- The deploy script requires a clean local git worktree.
- It syncs the current repository contents to the controller host with `rsync`.
- The installed service is a user service named `parameter-golf-autoresearch` by default.
- Service logs go to `~/.local/state/parameter-golf/` on the controller host.
- This bootstraps the controller host only. The GPU worker bootstrap belongs in the Runpod path.
