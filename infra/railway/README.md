# Railway Controller Deployment

This is the containerized controller-host path for Railway.

The deployment model is:

- Railway runs the long-lived autoresearch controller container.
- Runpod runs the disposable GPU worker.
- The controller talks to the worker over SSH.
- Railway persists the experiment repo and controller traces on a volume.

## Runtime Layout

The container now separates immutable controller code from mutable experiment
state:

- image code lives at `/opt/parameter-golf`
- the persistent work repo lives at `/var/lib/parameter-golf/repo`
- the controller runs from the image but points `REPO_DIR` at the persistent
  work repo

That matters because the controller makes local git commits to keep reviewed
changes and revert rejected ones. Those commits should survive restarts, but the
controller code itself should still be upgradeable with a normal image deploy.

## What The Image Contains

The root [Dockerfile](/var/home/carlulsoechristensen/Documents/parameter-golf/Dockerfile):

- installs `uv`
- installs the Codex CLI with `npm install -g @openai/codex`
- installs `git` and `openssh-client`
- copies the repo code into the image
- builds the `autoresearch` virtualenv during image build
- starts the controller via
  [controller-entrypoint.sh](/var/home/carlulsoechristensen/Documents/parameter-golf/infra/railway/controller-entrypoint.sh)

The root [railway.json](/var/home/carlulsoechristensen/Documents/parameter-golf/railway.json)
forces Dockerfile builds and an `ALWAYS` restart policy.

## Required Variables

Prepare the controller env from
[autoresearch.env.example](/var/home/carlulsoechristensen/Documents/parameter-golf/infra/railway/autoresearch.env.example).

The important ones are:

- `PGOLF_REPO_URL`
- `OPENAI_API_KEY`
- `REMOTE_HOST`
- `REMOTE_IDENTITY`
- `DATA_PATH`
- `TOKENIZER_PATH`

Optional SSH materialization is supported through:

- `SSH_PRIVATE_KEY`
- `SSH_KNOWN_HOSTS`
- `SSH_PRIVATE_KEY_BASE64`
- `SSH_KNOWN_HOSTS_BASE64`

The base64 variants are easier to store in a local env file that you pass to
the deploy helper.

## Automated Deploy

Use
[deploy_controller.py](/var/home/carlulsoechristensen/Documents/parameter-golf/infra/railway/deploy_controller.py)
to link the service, sync variables, ensure the volume exists, and deploy the
container with the Railway CLI.

Example:

```bash
python3 infra/railway/deploy_controller.py \
  --project <project-id> \
  --environment production \
  --service parameter-golf-controller \
  --env-file infra/railway/autoresearch.env \
  --create-service \
  --ensure-volume
```

Notes:

- the script expects Railway CLI auth to already work, usually through
  `RAILWAY_TOKEN` or `railway login`
- the env file parser is single-line `KEY=VALUE`; use base64 for multiline
  secrets
- the service volume mount path defaults to `/var/lib/parameter-golf`

## Operational Notes

- `CONTROLLER_ARGS` defaults to `--forever`; set it to `--hours 8` for bounded
  runs.
- `PGOLF_FETCH_ON_BOOT=1` only does a `git fetch` in the persistent work repo;
  it does not rewrite local reviewed history.
- Railway volumes only support one attached volume per service, so this
  controller is intentionally a single-replica service.
- For programmatic Codex CLI workflows, API-key auth is the recommended mode in
  the OpenAI Codex auth docs.
