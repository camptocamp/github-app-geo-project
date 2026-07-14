# GitHub application geospatial project

This application will manage:

- The Changelog in the release
- The Backports
- The pull request checks
- Adding useful links into the pull request
- Manage auto review and auto merge of pull requests
- Workflow trigger between repository
- Delete old workflow jobs
- Check and fix pre-commit hooks with less limitation than pre-commit.ci

[project configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/PROJECT-CONFIG.md).

[application configuration wiki](https://github.com/camptocamp/github-app-geo-project/wiki/Application-configuration).

[application configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/APPLICATION-CONFIG.md).

## Environment variables

All settings are loaded via `pydantic-settings` with the `GHCI__` prefix.

### Application configuration

Each GitHub application is configured via `GHCI__APPLICATION__<name>__<property>`:

| Variable                                               | Description                          |
| ------------------------------------------------------ | ------------------------------------ |
| `GHCI__APPLICATION__<name>__GITHUB_APP_ID`             | GitHub App ID                        |
| `GHCI__APPLICATION__<name>__GITHUB_APP_PRIVATE_KEY`    | GitHub App private key               |
| `GHCI__APPLICATION__<name>__GITHUB_APP_URL`            | GitHub App installation URL          |
| `GHCI__APPLICATION__<name>__GITHUB_APP_ADMIN_URL`      | GitHub App admin URL                 |
| `GHCI__APPLICATION__<name>__GITHUB_APP_WEBHOOK_SECRET` | Webhook secret for this app          |
| `GHCI__APPLICATION__<name>__TITLE`                     | Display title                        |
| `GHCI__APPLICATION__<name>__DESCRIPTION`               | Display description                  |
| `GHCI__APPLICATION__<name>__MODULES`                   | Space-separated list of module names |

### Database

| Variable                         | Default                                                    | Description                     |
| -------------------------------- | ---------------------------------------------------------- | ------------------------------- |
| `GHCI__SQLALCHEMY__URL`          | `postgresql+asyncpg://postgresql:postgresql@db:5432/tests` | Database URL                    |
| `GHCI__SQLALCHEMY__POOL_RECYCLE` | `None`                                                     | Connection pool recycle seconds |
| `GHCI__SQLALCHEMY__POOL_SIZE`    | `None`                                                     | Connection pool size            |
| `GHCI__SQLALCHEMY__MAX_OVERFLOW` | `None`                                                     | Connection pool max overflow    |
| `GHCI__SQLALCHEMY__DB_SCHEMA`    | `ghci`                                                     | Database schema name            |

### Auth (c2casgiutils)

| Variable                           | Description                             |
| ---------------------------------- | --------------------------------------- |
| `C2C__AUTH__GITHUB__REPOSITORY`    | GitHub repository for auth              |
| `C2C__AUTH__GITHUB__SECRET`        | OAuth client secret                     |
| `C2C__AUTH__GITHUB__CLIENT_ID`     | OAuth client ID                         |
| `C2C__AUTH__GITHUB__CLIENT_SECRET` | OAuth client secret                     |
| `C2C__AUTH__TEST__USERNAME`        | Test username (bypasses GitHub auth)    |
| `C2C__HTTP`                        | Set to `true` to disable HTTPS redirect |
| `C2C__PROMETHEUS__PORT`            | Prometheus metrics HTTP server port     |

### Other settings

| Variable                | Default                  | Description                                                          |
| ----------------------- | ------------------------ | -------------------------------------------------------------------- |
| `GHCI__SERVICE_URL`     | `http://localhost:8080/` | Base URL of the service                                              |
| `GHCI__SESSION_SECRET`  | `change-me`              | Session secret key                                                   |
| `GHCI__CONFIGURATION`   | `None`                   | Path to YAML configuration file                                      |
| `GHCI__TEST__APP_NAME`  | `None`                   | Test application name (enables test mode)                            |
| `GHCI__REDIS__HOST`     | `None`                   | Redis host                                                           |
| `GHCI__REDIS__PORT`     | `6379`                   | Redis port                                                           |
| `GHCI__REDIS__DB`       | `0`                      | Redis database number                                                |
| `GHCI__REDIS__USERNAME` | `None`                   | Redis username                                                       |
| `GHCI__REDIS__PASSWORD` | `None`                   | Redis password                                                       |
| `GHCI__REDIS__OPTIONS`  | `None`                   | Redis connection options, e.g. `ssl_cert_reqs=None,socket_timeout=5` |

### Duration format

Duration fields support ISO 8601 (`PT3H`, `P30D`, `PT600S`) and short formats (`2h30`, `2m30`, `1d`, `1w2d3h4m5s`).

### Database migration

When updating from a previous version, the `job_log` table needs a new column:

```sql
ALTER TABLE job_log ADD COLUMN css_style TEXT;
```

## Contributing

Install the pre-commit hooks:

```bash
pip install pre-commit
pre-commit install --allow-missing-config
```

The `prospector` tests should pass.

The code should be typed.

The code should be tested with `pytests`.
