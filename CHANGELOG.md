# Changelog

## 2026-09-09

### Added

- **Settings**: New `renovate_graph_max_old_space_size` setting (`GHCI__VERSIONS__RENOVATE_GRAPH_MAX_OLD_SPACE_SIZE`, default `3G`): the Node.js V8 heap limit (`--max-old-space-size`) used when running `renovate-graph`. It is passed via `NODE_OPTIONS` so the subprocess is not limited by Node.js's default heap size, which is derived from the host available memory (not the container limit) and can be lower than what big repositories need.

### Fixed

- **Versions**: Fix `renovate-graph` crashing with `FATAL ERROR: Ineffective mark-compacts near heap limit Allocation failed - JavaScript heap out of memory` (return code `-6`, a V8 heap abort, not a Kubernetes/container OOM kill) on large repositories. The subprocess is now run with `NODE_OPTIONS=--max-old-space-size=<MB>` derived from the `renovate_graph_max_old_space_size` setting.
- **Versions**: The `renovate-graph` executions are now serialized by an in-process lock: each instance can use up to `renovate_graph_max_old_space_size` (default `3G`) of Node.js heap, so two concurrent runs could exhaust the container memory. A `versions` job that needs `renovate-graph` while another one is running now waits for it to finish (a debug message is logged while waiting).

### Changed

- **Utils**: The subprocess log messages (`AnsiProcessMessage`) no more list the environment variables that are identical to the system ones. Since callers usually build the subprocess environment from a copy of `os.environ` plus a few overrides, only the added or overridden variables are now shown in the `Environment variable` section (the `TOKEN`/`KEY`/`SECRET` masking is preserved), which removes a lot of noise from the job logs.

### Fixed

- **UI**: The inline `<style>` element of the `versions` module repository dashboard (`/dashboard/versions?repository=<owner>/<repository>`) had no `nonce`, its rules (for example `.dep-unsupported`) were then blocked by the `style-src-elem` Content-Security-Policy directive. The dashboard view now transmits `request.state.nonce` to the module renderers: `render_template` has a new `nonce` parameter because the `Jinja2Templates` context processors are not applied to the `jinja2.Environment` it builds. This also fixes the `audit` module dashboard styles, which were rendered with an empty `nonce` and then blocked too.
- **UI**: The inline `style` attributes are no more emitted: the `limit` input of the `jobs` page filter form uses a rule of the `head_styles` block, and the HTML sanitizer no more allows the `style` attribute on the `a`, `span`, `p`, `div` and `em` elements. The inline style attributes cannot be nonced, they were already blocked by the Content-Security-Policy and allowing them would require an `'unsafe-inline'` source.

## 2026-09-08

### Fixed

- **Docker**: Fix `pyenv` not found at runtime (`FileNotFoundError: 'pyenv'` in the `audit` module): the production worker pods mount an `emptyDir` volume on `/var/www/.pyenv`, masking the `pyenv` sources cloned at that path in the image. `pyenv` is now installed in `/opt/pyenv` (`PYENV_ROOT`), and only the `/opt/pyenv/versions` sub folder should be mounted as a volume in production, so the lazily installed Python versions still survive the container restarts without masking the `pyenv` sources. The image also bakes a bootstrap `python` shim (replaced by the real shims on the first `pyenv install`), so the Snyk `--command=<pyenv root>/shims/python` arguments always resolve.
- **Cache clean**: The `pyenv` cache is cleaned at `<pyenv root>/cache` (based on `PYENV_ROOT`, `/opt/pyenv/cache` with the Docker image) instead of `~/.pyenv/cache`.
- **Queue**: Fix the event loop blocked for minutes by huge `HtmlMessage` log entries (for example the `versions` module dumping the full pygments-highlighted transversal status JSON). `HtmlMessage.to_plain_text` used a throwaway `html_sanitizer.Sanitizer` whose final `lxml` cleaner pass called `drop_tag()` on every element, which is quadratic for highlighted contents with thousands of sibling `<span>`, and every message was fully sanitized twice (console handler + job logs handler). The job timeout could not fire while the loop was blocked and the watchdog reported `event loop is blocked`. The plain text extraction is now linear, based on `html.parser` from the standard library, and the `to_html` / `to_plain_text` conversion results are cached per message instance.

### Added

- **Settings**: New `log_message_max_size` setting (`GHCI__LOG_MESSAGE_MAX_SIZE`, default `10000` characters): the HTML message contents are truncated, with a `... truncated (original size: N characters)` marker, in the log conversion paths (`to_html`, `to_plain_text` and the escaped fallback).

## 2026-09-07

### Changed

- **Docker**: The Python versions are no more compiled with `pyenv` during the image build. `pyenv` is cloned in `/var/www/.pyenv` (the default root, `$HOME/.pyenv`) and the versions are lazily installed on first use (by the `audit` module before `pyenv local`, and before running `prek` when the target repository has a `.python-version` file). The `PYENV_ROOT` environment variable is no more set in the image, `pyenv global` is set to `system`, and the CPython build dependencies (`zlib1g-dev`, `libreadline-dev`, `libssl-dev`, `libffi-dev`, `libsqlite3-dev`, `libbz2-dev`, `liblzma-dev`, `libncurses-dev`) stay installed for source builds.
- **Utils**: New `get_pyenv_root`, `pyenv_python_installed` and `ensure_pyenv_python` helpers. `create_commit_pull_request` installs a plain Python version (`X.Y` or `X.Y.Z`) from the repository `.python-version` before running `prek` (a pyenv virtualenv name is left untouched), and resolves the `pyenv` root without a `pyenv root` subprocess call.

### Added

- **Settings**: The `utils.timeouts.pyenv_install` timeout (`GHCI__UTILS__TIMEOUTS__PYENV_INSTALL`, default 30 minutes) for the lazy `pyenv install` calls.

### Fixed

- **Docker**: The image now installs Node.js 24 (instead of 22), as required by `renovate` 44 / `@jamietanna/renovate-graph` 0.40 (`engines.node: ^24.11.0`). On Node.js 22, the `versions` module failed at runtime with `TypeError: RegExp.escape is not a function`. The Docker build now runs `npm install` with `--engine-strict=true` and a smoke test importing the `renovate` module chain, so similar incompatibilities fail the image build instead of production jobs.

## 2026-09-02

### Changed

- **Clean**: The `clean` module now checks the folders existence on the target branch (with `git ls-tree`) before creating the worktree, and skips the worktree creation and the final `git push` when there is nothing to clean. Previously, a full worktree was created and a no-op push was done even when none of the folders to clean existed.

## 2026-08-31

### Changed

- **Audit**: Audit jobs of the same type on the same repository and branch are now deduplicated in the queue: when a new matching job is queued, the previous similar jobs in `new` status are replaced (marked as `skipped`), and their GitHub check runs are closed as `skipped`. For example, there can be at most one `snyk (1.21)` job in `new` status at a time for a repository.
- **Audit**: The `close-pull-request-issues` job names now include the pull request number, to avoid deduplicating jobs concerning different pull requests.
- **Audit**: The sub-job priorities are reordered to run the shortest jobs first: `cleanup` to `PRIORITY_STANDARD + 1` (31), `outdated` to `PRIORITY_STANDARD + 2` (32), `renovate` to `PRIORITY_STANDARD + 3` (33), the Snyk/dpkg fan-out to `PRIORITY_CRON` (40), `renovate (<version>)` to `PRIORITY_CRON + 1` (41), `dpkg (<version>)` to `PRIORITY_CRON + 2` (42) and `snyk (<version>)` to `PRIORITY_CRON + 3` (43, the slowest). The Snyk/dpkg fan-out priority is lowered from `PRIORITY_CRON + 10` to `PRIORITY_CRON`.
- **Queue**: The `jobs_unique_on` deduplication mechanism is now also applied to the jobs created from module actions and from dashboard issue edits, previously only the jobs created by the dispatcher were deduplicated.

## 2026-08-28

### Changed

- **Audit**: The `pre-commit` subprocess call in the audit module is replaced by `prek`. The audit timeout setting is renamed from `precommit` to `prek` (`GHCI__AUDIT__TIMEOUTS__PREK`).
- **Utils**: `create_commit_pull_request` parameters are renamed from `enable_pre_commit` / `skip_pre_commit_hooks` to `enable_prek` / `skip_prek_hooks`. The redundant `pre-commit` subprocess call is removed; only `prek` is now executed.
- **CI**: GitHub Actions workflow now uses `prek run` instead of `pre-commit run`, with cache keys and artifact names updated accordingly.

### Removed

- **Dependencies**: `pre-commit` is no longer a dependency. `prek` (already present) is the sole Git hook manager.
- **Settings**: The `_UtilsTimeouts.precommit_run` field is removed (redundant with `prek_run`).
- **Settings**: The `_CacheCleanSettings.pre_commit_max_size` field is removed (redundant with `prek_max_size`). The `~/.cache/pre-commit` cache entry is removed from the `cache-clean` module.

## 2026-08-27

### Added

- **Patch**: When the direct push to a protected branch (e.g. `main`) is rejected, the `patch` module now automatically creates a new branch (`ghci/patch/<branch>-<run-id>`) and opens a pull request with auto-merge enabled, instead of failing silently.

### Fixed

- **Queue**: Fix `MissingGreenlet` error on `SIGINT`/`SIGTERM` caused by `HandleSigint` replacing the correct `handle_signal` handler and attempting synchronous database operations in the async event loop. The existing `_requeue_cancelled_job` + `finally` cleanup in `_process_one_job` now handles job re-queueing on shutdown.
- **Queue**: Wrap `session.commit()` in the `_process_one_job` `finally` block with `asyncio.shield()` to protect the commit from being cancelled by a second signal during shutdown.

### Changed

- **Settings**: The `sqlalchemy.url` setting now uses `postgresql+asyncpg://` directly. The `sync_url` and `async_url` computed properties are removed. Update `GHCI__SQLALCHEMY__URL` environment variable to include the `+asyncpg` driver prefix.

### Removed

- **Dependencies**: `psycopg2` is no longer a dependency (the synchronous PostgreSQL driver was only used by the removed `HandleSigint` signal handler).

## 2026-08-26

### Fixed

- **Patch**: The `patch` module no longer crashes on `workflow_job` webhooks whose job steps carry a `pending` status (e.g. `completed.workflow_job.steps[i].status`). Such payloads previously raised a Pydantic `ValidationError` because `githubkit-schemas` only accepts `queued`, `in_progress` or `completed`. Invalid step statuses are now rewritten to `queued` before parsing.

## 2026-08-25

### Added

- **Queue**: On shutdown (`SIGTERM` or `SIGINT`), the interrupted jobs are now logged (visible in the job logs) and put back to `new`, to be reprocessed on the next start, instead of being definitively marked as `fail`.
- **Queue**: Log a message when the process starts and when a shutdown signal is received, to easily find the restarts in the container logs.
- **Health check**: `ghci-health-check` now prints explicit messages when the process-queue event loop seems blocked (warning from half of the timeout, error when marking the container as unhealthy).
- **Health check**: `ghci-health-check` now uses `py-spy` to dump real-time stack traces of the `process-queue` process when the event loop appears blocked, showing exactly where each thread is stuck.

### Changed

- **Queue**: `SIGTERM` and `SIGINT` now use the same graceful shutdown path: the tasks are cancelled and waited (the jobs cleanup, logs flush and commit are done), instead of abruptly stopping the event loop.
- **Health check**: `ghci-health-check` uses `py-spy dump` instead of `cat /var/ghci/job_info` for diagnostic output, providing real-time OS-level thread stack traces even when the event loop is completely frozen.

### Removed

- **Queue**: The synchronous `SIGINT` handler is replaced by the unified graceful shutdown path (it also did a blocking database call in the event loop).
- **Queue**: The `/var/ghci/job_info` file is no longer written by `_PrometheusWatch`. The health check now relies on `py-spy` for real-time stack traces, making the periodically-updated job info file obsolete.

## 2026-08-24

### Added

- **Versions**: The `renovate-graph` subprocess log level is now configurable via `GHCI__VERSIONS__RENOVATE_GRAPH_LOG_LEVEL` (default: `info`). Set to `debug` to get Renovate debug logs.

## 2026-08-22

### Added

- **Settings**: All hardcoded timeouts across modules are now configurable through Pydantic settings, organized per module (`settings.<module>.timeouts.<operation>`). Affected modules: `utils`, `audit`, `versions`, `clean`, `backport`, `cache_clean`, `tests`, `pull_request`, `patch`.

### Changed

- **Breaking**: `settings.audit_timeouts` moved to `settings.audit.timeouts`. Environment variables change from `GHCI__AUDIT_TIMEOUTS__*` to `GHCI__AUDIT__TIMEOUTS__*`.

## 2026-08-17

### Added

- **Queue**: Added step logs in the job preamble (`Get GitHub application`, `Get GitHub project`, `Get GitHub rate limit`, `Get dashboard issue`, `Get project configuration`, check run creation/update), to identify where a job hangs when it never reaches the module processing.

### Fixed

- **Versions module**: The subprocesses (`git ls-files`, `renovate-graph`) are now killed when their timeout expires, instead of being left running.
- **Queue**: The job selection now only locks the picked row (`LIMIT 1` added to the `FOR UPDATE SKIP LOCKED` query), instead of locking all the jobs of the current priority level for the whole selection transaction, which could make the other workers see no available job.
- **Database**: The SQLAlchemy connection pools now test the connections before using them (`pool_pre_ping` enabled by default, can be disabled with `GHCI__SQLALCHEMY__POOL_PRE_PING=false`), to not hang on dead connections (for example after a database restart).

## 2026-08-11

### Fixed

- **Modules & Queue**: Replaced blocking synchronous file I/O calls (`pathlib.Path.exists()`, `pathlib.Path.mkdir()`, `open()`, `shutil.rmtree()`, `tempfile.mkdtemp()`, `os.chdir()`, `c2cciutils.get_config()`) with async equivalents (`anyio.Path`, `anyio.to_thread.run_sync`) across all modules. This prevents the event loop from being blocked, allowing the `asyncio.timeout()` (50 min) to properly fire and cancel stuck jobs instead of leaving them in `PENDING` status permanently.
- **Queue**: Added explicit task cleanup after `asyncio.timeout()` fires to ensure the inner processing task is cancelled and its resources are released.

## 2026-08-10

### Changed

- **Patch module**: When `git push` fails, the module now returns `ProcessOutput(success=False)` instead of raising `PatchError`. This marks the job as `REPORT_ERROR` (yellow warning) instead of `FAIL` (red danger), distinguishing a push failure from a system error.

## 2026-07-20

### Added

- **Audit module**: Added configurable `dashboard-severity-threshold` (default: `medium`) and `advisory-severity-threshold` (default: `high`) to `snyk` configuration.
- **Audit module**: Added `excluded-files` configuration option to exclude specific files (regex patterns) from the dashboard and advisory creation.
- **Audit module**: Vulnerabilities in the issue dashboard are now grouped by file with `==== <file_name>` headers under `=== <version>` section titles.
- **Audit module**: The module now automatically creates GitHub Security Advisories for vulnerabilities meeting the `advisory-severity-threshold` (requires `security_advisories: write` permission).
- **Audit module**: Added `_VulnerabilityData` structured data class and `SEVERITY_ORDER` ordering, `ECOSYSTEM_MAP` for Snyk-to-GitHub ecosystem mapping.

## 2026-07-15

### Added

- **Admin access**: GitHub OAuth users can now be granted admin status based on their repository permissions. Configured via `C2C__AUTH__GITHUB__REPOSITORY` and `C2C__AUTH__GITHUB__ACCESS_TYPE` (default: `pull`). Set `C2C__AUTH__GITHUB__ACCESS_TYPE=admin` to require admin permissions on the repository.

## 2026-07-08

### Changed

- **Framework migration**: replaced Pyramid WSGI + `c2cwsgiutils` with FastAPI ASGI + `c2casgiutils`.
- **Web server**: replaced `waitress` + `gunicorn` with `uvicorn`.
- **Templates**: converted all Mako templates to Jinja2.
- **Configuration**: environment variables are now centralized via `pydantic-settings` with the `GHCI__` prefix.
  - All application-specific settings are grouped under `GHCI__APPLICATION__<name>__<property>` (e.g. `GHCI__APPLICATION__TEST__GITHUB_APP_ID`).
  - Old flat env vars (`LOG_LEVEL`, `SQL_LOG_LEVEL`, `SERVICE_URL`, `VISIBLE_ENTRY_POINT`, `TEST_APPLICATION`, `TEST_USER`, `GHCI_APPLICATIONS`, `GHCI_TEST_*`) are removed or replaced.
  - The `C2C_AUTH_GITHUB_*` vars have been updated to `C2C__AUTH__GITHUB__*` format.
  - `C2C_PROMETHEUS_PORT` → `C2C__PROMETHEUS__PORT`.
  - `SQLALCHEMY_URL` → `GHCI__SQLALCHEMY__URL` (now uses `postgresql+asyncpg://`).
  - Duration fields now accept ISO 8601 format (`PT3H`, `P30D`, `PT600S`) and combined short formats (`2h30`, `2m30`, `1w2d`).
  - Redis settings are now under `settings.redis.*`.
  - Webhook settings are under `settings.webhook.*`.
  - Module-specific settings are grouped: `settings.audit.*`, `settings.versions.*`, `settings.dispatch_publishing.*`, `settings.process_queue.*`.
  - `settings.application_settings` property removed; use `settings.application_configs` directly.
- **Dependencies**: `itsdangerous` added as explicit dependency (required by `SessionMiddleware`).
- **Security**:
  - Authentication types are now an `AuthType` enum.
  - `X-Hub-Signature-256` validation is now handled exclusively in `security.py`.
  - CSP headers are enforced via `ArmorHeaderMiddleware`; inline scripts and styles use `CSP_NONCE`.
  - All inline styles moved to CSS classes.
  - ANSI log messages now use CSS classes instead of inline styles.
  - Repository-level permission checks (`has_repo_access`) restored for `logs_view`, `output_view`, and `project_view`.
- **Database**: `JobLogEntry` gained a `css_style` column to store ANSI CSS styles alongside log entries.
- **Logging**: root logger level is temporarily set to `DEBUG` during job processing so that INFO/DEBUG messages are captured in the job log.
- **Duration parsing**: consolidated in `settings.py`; supports ISO 8601 and combined short formats (e.g. `2h30`, `2m30`).
- `_AppConfig` model now properly passes `title`, `description`, `github_app.url`, `github_app.admin_url`, `github_app.webhook_secret` from environment variables.
- The `color` field in `_DependencyBase` and `_Dependencies` models was renamed to `css_class` and now holds CSS class names instead of CSS variable names.

### Added

- **Health checks**: SQLAlchemy and Redis health checks registered via `c2casgiutils.health_checks`.
- **Prometheus**: metrics instrumentation via `prometheus_fastapi_instrumentator.Instrumentator` and Prometheus HTTP server.
- **Sentry**: error tracking initialized if DSN is configured.
- **Logging**: `_LOGGER` module-level logger convention documented in `AGENTS.md`.
- Debug log of all settings at application startup (`LOG_LEVEL=DEBUG`).
- Tests for `merge_css_blocks` and `_to_html_css` functions.

### Removed

- `c2cwsgiutils` dependency completely replaced by `c2casgiutils`.
- `production.ini` and `gunicorn.conf.py` configuration files.
- `requirements.txt` restored (was deleted during migration).
- `app.state.settings` and `app.state.db_url` — use `settings` directly.
- `attrdict` dependency removed.
- `pkg_resources` replaced with `importlib.metadata.entry_points`.

### Fixed

- Jinja2 operator precedence: parenthesize `(a - b) | filter` to avoid `a - (b | filter)`.
- Template filter registration: `markdown`, `sanitizer`, `pprint_date`, `pprint_short_date`, `pprint_full_date`, `pprint_duration` are now registered as Jinja2 filters (not just globals).
- `pprint_date` and `markdown` now return `Markup` objects to avoid double-escaping.
- `markdown` filter handles `None` input.
- Dark mode `data-bs-theme` attribute now works thanks to CSP nonce support.
- `test_pprint_duration` uses `timedelta` objects instead of string literals.
- Acceptance test reference images updated to match Jinja2 rendering.

### Migration notes

- **Database**: After deploying this version, run the following SQL to add the `css_style` column to `job_log`:
  ```sql
  ALTER TABLE job_log ADD COLUMN css_style TEXT;
  ```
- **Environment variables**: See the updated `README.md` for the new environment variable format.
