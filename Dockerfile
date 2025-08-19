# Base of all section, install the apt packages
FROM ubuntu:24.04 AS base-all
LABEL maintainer Camptocamp "info@camptocamp.com"

# Fail on error on pipe, see: https://github.com/hadolint/hadolint/wiki/DL4006.
# Treat unset variables as an error when substituting.
# Print commands and their arguments as they are executed.
SHELL ["/bin/bash", "-o", "pipefail", "-cux"]

RUN --mount=type=cache,target=/var/lib/apt/lists \
    --mount=type=cache,target=/var/cache,sharing=locked \
    apt-get update \
    && apt-get upgrade --assume-yes \
    && apt-get install --assume-yes --no-install-recommends python3-pip python3-venv postgresql-client docker.io libmagic1 git curl gnupg zlib1g libpq5 \
    && python3 -m venv /venv

ENV PATH=/venv/bin:$PATH

# Used to convert the locked packages by poetry to pip requirements format
# We don't directly use `poetry install` because it force to use a virtual environment.
FROM base-all AS poetry

# Fail on error on pipe, see: https://github.com/hadolint/hadolint/wiki/DL4006.
# Treat unset variables as an error when substituting.
# Print commands and their arguments as they are executed.
SHELL ["/bin/bash", "-o", "pipefail", "-cux"]

# Install Poetry
WORKDIR /tmp
COPY requirements.txt ./
RUN --mount=type=cache,target=/root/.cache \
    python3 -m pip install --disable-pip-version-check --requirement=requirements.txt

# Do the conversion
COPY poetry.lock pyproject.toml ./
ENV POETRY_DYNAMIC_VERSIONING_BYPASS=0.0.0
RUN poetry export --output=requirements.txt \
    && poetry export --with=dev --output=requirements-dev.txt

# Base, the biggest thing is to install the Python packages
FROM base-all AS base

# Fail on error on pipe, see: https://github.com/hadolint/hadolint/wiki/DL4006.
# Treat unset variables as an error when substituting.
# Print commands and their arguments as they are executed.
SHELL ["/bin/bash", "-o", "pipefail", "-cux"]

COPY .nvmrc /tmp
RUN --mount=type=cache,target=/var/lib/apt/lists \
    --mount=type=cache,target=/var/cache,sharing=locked \
    NODE_MAJOR="$(cat /tmp/.nvmrc)" \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" >/etc/apt/sources.list.d/nodesource.list \
    && curl --silent https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor --output=/etc/apt/keyrings/nodesource.gpg \
    && apt-get update \
    && apt-get install --assume-yes --no-install-recommends "nodejs=${NODE_MAJOR}.*"

# Install required packages
RUN --mount=type=cache,target=/var/lib/apt/lists \
    --mount=type=cache,target=/var/cache,sharing=locked \
    apt-get update \
    && apt-get install --assume-yes --no-install-recommends build-essential python3-dev libpq-dev tree

    RUN --mount=type=cache,target=/var/cache,sharing=locked \
    --mount=type=cache,target=/root/.cache \
    --mount=type=bind,from=poetry,source=/tmp,target=/poetry \
    python3 -m pip install --disable-pip-version-check --no-deps --requirement=/poetry/requirements.txt \
    && python3 -m compileall /usr/local/lib/python* /usr/lib/python*

# Install packages required by audit
RUN --mount=type=cache,target=/var/lib/apt/lists \
    --mount=type=cache,target=/var/cache,sharing=locked \
    apt-get update \
    && apt-get install --assume-yes --no-install-recommends libproj-dev pkgconf libcairo2-dev libgraphviz-dev unzip \
    default-jre-headless openjdk-11-jre-headless openjdk-17-jre-headless openjdk-21-jre-headless

# From c2cwsgiutils

CMD ["gunicorn", "--paste=/app/production.ini"]

ENV LOG_TYPE=console \
    DEVELOPMENT=0 \
    PKG_CONFIG_ALLOW_SYSTEM_LIBS=OHYESPLEASE

ENV C2C_SECRET= \
    C2C_BASE_PATH=/c2c \
    C2C_REDIS_URL= \
    C2C_REDIS_SENTINELS= \
    C2C_REDIS_TIMEOUT=3 \
    C2C_REDIS_SERVICENAME=mymaster \
    C2C_REDIS_DB=0 \
    C2C_BROADCAST_PREFIX=broadcast_api_ \
    C2C_REQUEST_ID_HEADER= \
    C2C_REQUESTS_DEFAULT_TIMEOUT= \
    C2C_SQL_PROFILER_ENABLED=0 \
    C2C_PROFILER_PATH= \
    C2C_PROFILER_MODULES= \
    C2C_DEBUG_VIEW_ENABLED=0 \
    C2C_ENABLE_EXCEPTION_HANDLING=0 \
    LOG_LEVEL=INFO \
    ASYNCIO_LOG_LEVEL=DEBUG \
    GUNICORN_LOG_LEVEL=INFO \
    C2CWSGIUTILS_LOG_LEVEL=INFO \
    SQL_LOG_LEVEL=INFO \
    OTHER_LOG_LEVEL=INFO

# End from c2cwsgiutils

EXPOSE 8080

WORKDIR /app/

# The final part
FROM base AS runner

ENV PATH=/pyenv/shims:/pyenv/bin:/var/www/.local/bin/:${PATH} \
    PYENV_ROOT=/pyenv

# Install different Python version with pyenv
# hadolint ignore=SC2086
RUN --mount=type=cache,target=/var/lib/apt/lists \
    --mount=type=cache,target=/var/cache,sharing=locked \
    DEV_PACKAGES="libcurses-ocaml-dev libreadline-dev" \
    && apt-get update \
    && apt-get install --assume-yes --no-install-recommends ${DEV_PACKAGES} \
    && git clone --depth=1 https://github.com/pyenv/pyenv.git /pyenv \
    && pyenv install 3.7 3.8 3.9 3.10 3.11 3.12 \
    && apt-get remove --purge --autoremove --yes ${DEV_PACKAGES}

ENV PATH=${PATH}:/app/node_modules/.bin

COPY package.json package-lock.json ./
RUN --mount=type=cache,target=/root/.cache \
    npm install \
    && ln -s node_modules/@jamietanna/renovate-graph/patches/ . \
    && node_modules/.bin/patch-package

COPY . /app/
ARG VERSION=dev
RUN --mount=type=cache,target=/root/.cache \
    POETRY_DYNAMIC_VERSIONING_BYPASS=${VERSION} python3 -m pip install --disable-pip-version-check --no-deps --editable=. \
    && python3 -m compileall

# Set the default Python version to 3.10 (version present on Ubuntu LTS))
RUN pyenv global 3.10 \
    && chmod a+rw -R /pyenv/

# Create the home of www-data
RUN mkdir /var/www \
    && chmod a+rwx /var/www \
    && chown -R 33:33 /var/www

RUN mkdir -p /prometheus-metrics \
    && chmod a+rwx /prometheus-metrics

ENV PROMETHEUS_MULTIPROC_DIR=/prometheus-metrics

# Do the lint, used by the tests
FROM base AS tests

# Fail on error on pipe, see: https://github.com/hadolint/hadolint/wiki/DL4006.
# Treat unset variables as an error when substituting.
# Print commands and their arguments as they are executed.
SHELL ["/bin/bash", "-o", "pipefail", "-cux"]

RUN --mount=type=cache,target=/var/lib/apt/lists \
    --mount=type=cache,target=/var/cache,sharing=locked \
    apt-get install --assume-yes --no-install-recommends \
    libglib2.0-0 libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2t64

RUN --mount=type=cache,target=/root/.cache \
    --mount=type=bind,from=poetry,source=/tmp,target=/poetry \
    python3 -m pip install --disable-pip-version-check --no-deps --requirement=/poetry/requirements-dev.txt

# hadolint ignore=DL3003
RUN cd /venv/lib/python3.12/site-packages/c2cwsgiutils/acceptance/ && npm install

COPY . ./
RUN --mount=type=cache,target=/root/.cache \
    POETRY_DYNAMIC_VERSIONING_BYPASS=0.0.0 python3 -m pip install --disable-pip-version-check --no-deps --editable=. \
    && python3 -m pip freeze >/requirements.txt

COPY scripts/* /usr/bin/

RUN mkdir -p /var/ghci \
    && chmod a+rwx /var/ghci


# Set runner as final
FROM runner
