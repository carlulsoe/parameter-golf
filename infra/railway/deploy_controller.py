#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def shell_join(parts: list[str]) -> str:
    return " ".join(shlex.quote(part) for part in parts)


def railway_base_cmd() -> list[str]:
    raw = os.environ.get("RAILWAY_CLI", "npx -y @railway/cli@latest")
    parts = shlex.split(raw)
    if not parts:
        raise SystemExit("RAILWAY_CLI is empty")
    return parts


def run(
    cmd: list[str],
    *,
    cwd: Path,
    dry_run: bool = False,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    print("+", shell_join(cmd))
    if dry_run:
        return subprocess.CompletedProcess(cmd, 0, "", "")
    return subprocess.run(
        cmd,
        cwd=cwd,
        input=input_text,
        text=True,
        capture_output=True,
        check=True,
    )


def capture_json(
    cmd: list[str],
    *,
    cwd: Path,
) -> Any:
    result = subprocess.run(
        cmd,
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(result.stdout)


def require_clean_git(root: Path) -> None:
    result = subprocess.run(
        ["git", "status", "--short"],
        cwd=root,
        text=True,
        capture_output=True,
        check=True,
    )
    if result.stdout.strip():
        raise SystemExit("local git worktree must be clean before Railway deployment")


def parse_env_file(path: Path) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)=(.*)", raw_line)
        if not match:
            raise SystemExit(f"invalid env line: {raw_line}")
        key, rhs = match.group(1), match.group(2)
        tokens = shlex.split(f"{key}={rhs}", posix=True)
        if len(tokens) != 1 or "=" not in tokens[0]:
            raise SystemExit(f"invalid env assignment: {raw_line}")
        parsed_key, parsed_value = tokens[0].split("=", 1)
        pairs.append((parsed_key, parsed_value))
    return pairs


def has_mount_path(payload: Any, mount_path: str) -> bool:
    if isinstance(payload, dict):
        for key, value in payload.items():
            if key in {"mountPath", "mount_path"} and value == mount_path:
                return True
            if has_mount_path(value, mount_path):
                return True
        return False
    if isinstance(payload, list):
        return any(has_mount_path(item, mount_path) for item in payload)
    return False


def run_railway(
    base_cmd: list[str],
    args: list[str],
    *,
    cwd: Path,
    dry_run: bool = False,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    return run([*base_cmd, *args], cwd=cwd, dry_run=dry_run, input_text=input_text)


def link_service(args: argparse.Namespace, base_cmd: list[str], root: Path) -> None:
    cmd = [
        "link",
        "--project",
        args.project,
        "--environment",
        args.environment,
        "--service",
        args.service,
    ]
    if args.workspace:
        cmd.extend(["--workspace", args.workspace])
    run_railway(base_cmd, cmd, cwd=root, dry_run=args.dry_run)


def maybe_create_service(args: argparse.Namespace, base_cmd: list[str], root: Path) -> None:
    if not args.create_service:
        return
    cmd = ["add", "--service", args.service]
    if args.dry_run:
        run_railway(base_cmd, cmd, cwd=root, dry_run=True)
        return
    try:
        run_railway(base_cmd, cmd, cwd=root)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.stderr.strip() or str(exc)) from exc


def set_variables(
    args: argparse.Namespace,
    base_cmd: list[str],
    root: Path,
    pairs: list[tuple[str, str]],
) -> None:
    for key, value in pairs:
        cmd = [
            "variable",
            "set",
            "--service",
            args.service,
            "--environment",
            args.environment,
            "--skip-deploys",
        ]
        if "\n" in value:
            cmd.extend(["--stdin", key])
            run_railway(base_cmd, cmd, cwd=root, dry_run=args.dry_run, input_text=value)
            continue
        cmd.append(f"{key}={value}")
        run_railway(base_cmd, cmd, cwd=root, dry_run=args.dry_run)


def ensure_volume(args: argparse.Namespace, base_cmd: list[str], root: Path) -> None:
    if not args.ensure_volume:
        return
    if args.dry_run:
        run_railway(
            base_cmd,
            [
                "volume",
                "add",
                "--service",
                args.service,
                "--environment",
                args.environment,
                "--mount-path",
                args.volume_mount_path,
            ],
            cwd=root,
            dry_run=True,
        )
        return
    payload = capture_json(
        [
            *base_cmd,
            "volume",
            "list",
            "--service",
            args.service,
            "--environment",
            args.environment,
            "--json",
        ],
        cwd=root,
    )
    if has_mount_path(payload, args.volume_mount_path):
        return
    run_railway(
        base_cmd,
        [
            "volume",
            "add",
            "--service",
            args.service,
            "--environment",
            args.environment,
            "--mount-path",
            args.volume_mount_path,
        ],
        cwd=root,
    )


def deploy(args: argparse.Namespace, base_cmd: list[str], root: Path) -> None:
    cmd = [
        "up",
        "--ci",
        "--service",
        args.service,
        "--environment",
        args.environment,
        "--project",
        args.project,
        "--message",
        args.message,
    ]
    if args.detach:
        cmd.append("--detach")
    run_railway(base_cmd, cmd, cwd=root, dry_run=args.dry_run)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deploy the Railway controller service for Parameter Golf autoresearch."
    )
    parser.add_argument("--project", required=True, help="Railway project ID")
    parser.add_argument("--environment", required=True, help="Railway environment ID or name")
    parser.add_argument("--service", required=True, help="Railway service ID or name")
    parser.add_argument("--workspace", default=None, help="Optional Railway workspace ID")
    parser.add_argument(
        "--env-file",
        type=Path,
        required=True,
        help="Local env file to sync into Railway service variables",
    )
    parser.add_argument(
        "--create-service",
        action="store_true",
        help="Create the Railway service before linking and deploying",
    )
    parser.add_argument(
        "--ensure-volume",
        action="store_true",
        help="Create the service volume if the requested mount path is not present",
    )
    parser.add_argument(
        "--volume-mount-path",
        default="/var/lib/parameter-golf",
        help="Railway volume mount path for persistent controller state",
    )
    parser.add_argument(
        "--message",
        default="Deploy Parameter Golf autoresearch controller",
        help="Deployment message passed to `railway up`",
    )
    parser.add_argument(
        "--detach",
        action="store_true",
        help="Do not attach to the Railway deploy log stream",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print Railway commands without executing them",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    root = repo_root()
    env_file = args.env_file.resolve()
    if not env_file.exists():
        raise SystemExit(f"env file does not exist: {env_file}")
    require_clean_git(root)
    base_cmd = railway_base_cmd()
    pairs = parse_env_file(env_file)

    maybe_create_service(args, base_cmd, root)
    link_service(args, base_cmd, root)
    set_variables(args, base_cmd, root, pairs)
    ensure_volume(args, base_cmd, root)
    deploy(args, base_cmd, root)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        if exc.stderr:
            raise SystemExit(exc.stderr.strip()) from exc
        raise SystemExit(exc.returncode) from exc
