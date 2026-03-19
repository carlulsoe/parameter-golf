FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    UV_LINK_MODE=copy \
    PGOLF_CODE_DIR=/opt/parameter-golf \
    PGOLF_RUNTIME_ROOT=/var/lib/parameter-golf \
    PGOLF_REPO_DIR=/var/lib/parameter-golf/repo \
    PGOLF_REPO_BRANCH=main \
    CONTROLLER_ARGS=--forever

RUN apt-get update \
    && apt-get install --yes --no-install-recommends \
        bash \
        ca-certificates \
        git \
        nodejs \
        npm \
        openssh-client \
    && rm -rf /var/lib/apt/lists/*

RUN pip install uv
RUN npm install -g @openai/codex

COPY . /opt/parameter-golf
RUN uv sync --frozen --no-dev --directory /opt/parameter-golf/autoresearch

COPY infra/railway/controller-entrypoint.sh /usr/local/bin/controller-entrypoint.sh
RUN chmod +x /usr/local/bin/controller-entrypoint.sh

WORKDIR /opt/parameter-golf
VOLUME ["/var/lib/parameter-golf"]

ENTRYPOINT ["/usr/local/bin/controller-entrypoint.sh"]
