# Changelog

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
