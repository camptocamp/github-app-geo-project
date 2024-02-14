# Base of all section, install the apt packages
FROM ghcr.io/osgeo/gdal:ubuntu-small-3.8.0 as base-all
LABEL maintainer Camptocamp "info@camptocamp.com"

# Fail on error on pipe, see: https://github.com/hadolint/hadolint/wiki/DL4006.
# Treat unset variables as an error when substituting.
# Print commands and their arguments as they are executed.
SHELL ["/bin/bash", "-o", "pipefail", "-cux"]

RUN --mount=type=cache,target=/var/lib/apt/lists \
    --mount=type=cache,target=/var/cache,sharing=locked \
    apt-get update \
    && apt-get upgrade --assume-yes \
    && apt-get install --assume-yes --no-install-recommends \
        libmapnik3.1 mapnik-utils \
        libdb5.3 \
        fonts-dejavu \
        optipng jpegoptim \
        postgresql-client net-tools iputils-ping \
        python3-pip

# Used to convert the locked packages by poetry to pip requirements format
# We don't directly use `poetry install` because it force to use a virtual environment.
FROM base-all as poetry

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
FROM base-all as base

# hadolint ignore=SC2086,DL3042,DL3008
RUN --mount=type=cache,target=/var/lib/apt/lists \
    --mount=type=cache,target=/var/cache,sharing=locked \
    --mount=type=cache,target=/root/.cache \
    --mount=type=bind,from=poetry,source=/tmp,target=/poetry \
    DEV_PACKAGES="python3-dev libpq-dev build-essential" \
    && apt-get update \
    && apt-get install --assume-yes --no-install-recommends ${DEV_PACKAGES} \
    && python3 -m pip install --disable-pip-version-check --no-deps --requirement=/poetry/requirements.txt \
    && python3 -m compileall /usr/local/lib/python* /usr/lib/python* \
    && apt-get remove --purge --autoremove --yes ${DEV_PACKAGES} binutils

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
    C2C_ENABLE_EXCEPTION_HANDLING=0

# End from c2cwsgiutils

EXPOSE 8080

WORKDIR /app/

# The final part
FROM base as runner

COPY . /app/
ARG VERSION=dev
ENV POETRY_DYNAMIC_VERSIONING_BYPASS=dev
RUN --mount=type=cache,target=/root/.cache \
    POETRY_DYNAMIC_VERSIONING_BYPASS=${VERSION} python3 -m pip install --disable-pip-version-check --no-deps --editable=. \
    && python3 -m compileall

RUN mkdir -p /prometheus-metrics \
    && chmod a+rwx /prometheus-metrics
ENV PROMETHEUS_MULTIPROC_DIR=/prometheus-metrics

# Do the lint, used by the tests
FROM base as tests

# Fail on error on pipe, see: https://github.com/hadolint/hadolint/wiki/DL4006.
# Treat unset variables as an error when substituting.
# Print commands and their arguments as they are executed.
SHELL ["/bin/bash", "-o", "pipefail", "-cux"]

RUN --mount=type=cache,target=/var/lib/apt/lists \
    --mount=type=cache,target=/var/cache,sharing=locked \
    apt-get install --assume-yes --no-install-recommends git curl gnupg \
    libglib2.0-0 libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 libxcomposite1 \
    libxdamage1 libxfixes3 libxrandr2 libgbm1 libpango-1.0-0 libcairo2 libasound2

COPY .nvmrc /tmp
RUN --mount=type=cache,target=/var/lib/apt/lists \
    --mount=type=cache,target=/var/cache,sharing=locked \
    NODE_MAJOR="$(cat /tmp/.nvmrc)" \
    && echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_${NODE_MAJOR}.x nodistro main" > /etc/apt/sources.list.d/nodesource.list \
    && curl --silent https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor --output=/etc/apt/keyrings/nodesource.gpg \
    && apt-get update \
    && apt-get install --assume-yes --no-install-recommends "nodejs=${NODE_MAJOR}.*"

RUN --mount=type=cache,target=/root/.cache \
    --mount=type=bind,from=poetry,source=/tmp,target=/poetry \
    python3 -m pip install --disable-pip-version-check --no-deps --requirement=/poetry/requirements-dev.txt

COPY . ./
RUN --mount=type=cache,target=/root/.cache \
    POETRY_DYNAMIC_VERSIONING_BYPASS=0.0.0 python3 -m pip install --disable-pip-version-check --no-deps --editable=. \
    && python3 -m pip freeze > /requirements.txt

ENV TILEGENERATION_MAIN_CONFIGFILE=

# Set runner as final
FROM runner
