#!/usr/bin/env bash
set -euo pipefail

require_env() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "missing required env: ${name}" >&2
    exit 1
  fi
}

write_secret() {
  local path="$1"
  local raw_name="$2"
  local base64_name="$3"
  if [[ -n "${!base64_name:-}" ]]; then
    printf '%s' "${!base64_name}" | base64 --decode > "${path}"
    return
  fi
  if [[ -n "${!raw_name:-}" ]]; then
    printf '%s\n' "${!raw_name}" > "${path}"
  fi
}

prepare_ssh() {
  local ssh_dir="${HOME}/.ssh"
  mkdir -p "${ssh_dir}"
  chmod 700 "${ssh_dir}"

  write_secret "${ssh_dir}/id_ed25519" SSH_PRIVATE_KEY SSH_PRIVATE_KEY_BASE64
  if [[ -f "${ssh_dir}/id_ed25519" ]]; then
    chmod 600 "${ssh_dir}/id_ed25519"
  fi

  write_secret "${ssh_dir}/known_hosts" SSH_KNOWN_HOSTS SSH_KNOWN_HOSTS_BASE64
  if [[ -f "${ssh_dir}/known_hosts" ]]; then
    chmod 644 "${ssh_dir}/known_hosts"
  fi
}

prepare_repo() {
  local runtime_root="${PGOLF_RUNTIME_ROOT:-/var/lib/parameter-golf}"
  local repo_dir="${PGOLF_REPO_DIR:-${runtime_root}/repo}"
  local repo_url="${PGOLF_REPO_URL}"
  local branch="${PGOLF_REPO_BRANCH:-main}"

  mkdir -p "${runtime_root}"

  if [[ "${repo_url}" == /* && -d "${repo_url}" ]]; then
    git config --global --add safe.directory "${repo_url}"
    if [[ -d "${repo_url}/.git" ]]; then
      git config --global --add safe.directory "${repo_url}/.git"
    fi
  fi

  if [[ ! -d "${repo_dir}/.git" ]]; then
    git clone --branch "${branch}" --single-branch "${repo_url}" "${repo_dir}"
  fi

  git config --global --add safe.directory "${repo_dir}"
  git -C "${repo_dir}" remote set-url origin "${repo_url}" >/dev/null 2>&1 || true

  if [[ "${PGOLF_FETCH_ON_BOOT:-0}" == "1" ]]; then
    git -C "${repo_dir}" fetch origin --prune
  fi

  mkdir -p \
    "${repo_dir}/logs" \
    "${repo_dir}/controller_state" \
    "${repo_dir}/remote_logs"
}

run_controller() {
  local code_dir="${PGOLF_CODE_DIR:-/opt/parameter-golf}"
  local repo_dir="${PGOLF_REPO_DIR:-${PGOLF_RUNTIME_ROOT:-/var/lib/parameter-golf}/repo}"
  local controller_args="${CONTROLLER_ARGS:---forever}"

  cd "${code_dir}/autoresearch"
  export REPO_DIR="${repo_dir}"
  exec "${code_dir}/autoresearch/.venv/bin/python" run_pgolf_experiment.py ${controller_args}
}

main() {
  require_env PGOLF_REPO_URL
  require_env OPENAI_API_KEY
  prepare_ssh
  prepare_repo
  run_controller
}

main "$@"
