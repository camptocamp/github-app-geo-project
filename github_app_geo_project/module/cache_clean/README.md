# Cache clean module

Clean local tool caches (pip, poetry, pyenv, prek, pre-commit, npm) on the worker pod.

## Trigger

The module is triggered by the `cache-clean` event:

```bash
send-event --application=<app> --event=cache-clean
```

This should be configured as a separate Kubernetes CronJob, scheduled after the `daily` event
(which triggers audit and other modules).

## Priority

The module uses `PRIORITY_CRON + 10` to ensure it runs after standard cron jobs.

## Cache directories managed

| Directory                        | Tool               | Cleanup strategy                                                                   |
| -------------------------------- | ------------------ | ---------------------------------------------------------------------------------- |
| `~/.cache/pip/`                  | pip                | `pip cache purge`, then delete if still over limit                                 |
| `~/.cache/pypoetry/artifacts/`   | Poetry artifacts   | `poetry cache clear --all`, then delete if still over limit                        |
| `~/.cache/pypoetry/virtualenvs/` | Poetry virtualenvs | Delete directly                                                                    |
| `~/.pyenv/cache/`                | pyenv              | Delete directly                                                                    |
| `~/.cache/prek/`                 | prek               | Delete directly                                                                    |
| `~/.cache/pre-commit/`           | pre-commit         | Delete directly                                                                    |
| `~/.npm/`                        | npm                | `npm cache clean`, then `npm cache clean --force`, then delete if still over limit |

## Configuration

Thresholds are configured via environment variables in the
[`_CacheCleanSettings`](../../settings.py) class:

| Variable                                         | Default | Description                     |
| ------------------------------------------------ | ------- | ------------------------------- |
| `GHCI__CACHE_CLEAN__PIP_MAX_SIZE`                | `1000M` | Max size for pip cache          |
| `GHCI__CACHE_CLEAN__POETRY_ARTIFACTS_MAX_SIZE`   | `500M`  | Max size for poetry artifacts   |
| `GHCI__CACHE_CLEAN__POETRY_VIRTUALENVS_MAX_SIZE` | `500M`  | Max size for poetry virtualenvs |
| `GHCI__CACHE_CLEAN__PYENV_MAX_SIZE`              | `200M`  | Max size for pyenv cache        |
| `GHCI__CACHE_CLEAN__PREK_MAX_SIZE`               | `200M`  | Max size for prek cache         |
| `GHCI__CACHE_CLEAN__PRE_COMMIT_MAX_SIZE`         | `500M`  | Max size for pre-commit cache   |
| `GHCI__CACHE_CLEAN__NPM_MAX_SIZE`                | `500M`  | Max size for npm cache          |
| `GHCI__CACHE_CLEAN__LOG_MAX`                     | `10M`   | Max size of log file            |
| `GHCI__CACHE_CLEAN__LOG_BACKUP_COUNT`            | `5`     | Number of backup log files      |

Values are specified as a number followed by a unit:
`B`/`o` (bytes), `K`/`KB`/`KiB` (kibibytes), `M`/`MB`/`MiB` (mebibytes),
`G`/`GB`/`GiB` (gigabytes), `T`/`TB`/`TiB` (terabytes).
A plain number without unit is treated as bytes.

## Logging

A rotating log file is written to `~/.cache/clean.log` with up to 5 backup files (default 10 MB each).
Configured via:

- `GHCI__CACHE_CLEAN__LOG_MAX` (default: `10M`)
- `GHCI__CACHE_CLEAN__LOG_BACKUP_COUNT` (default: `5`)

## pip.conf

To configure pip cache behaviour, add the following to the Docker image:

```ini
[global]
cache-dir = ~/.cache/pip
cache-max-size = 1000  # [Mo]
```

## Persisted directories

The following directories should be persisted across worker pod restarts:

- `~/.cache`
- `~/.pyenv/cache`
- `~/.npm`
