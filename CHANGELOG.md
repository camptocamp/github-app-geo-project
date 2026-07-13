# Changelog

## 0.1.0 — 2026-07-08

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
