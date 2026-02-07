## Modules

The modules are stored in `github_app_geo_project/module/<name>/`.

If needed the module has a JSON schema configuration schema stored in `github_app_geo_project/module/<name>/schema.json`.

Each modules should have a complete end user documentation in a markdown file usually `github_app_geo_project/module/<name>/README.md`.

The modules should be reasonably tested in `tests/test_module_<name>.py`.

## Important classes

### ProcessContext

The `github_app_geo_project.module.ProcessContext` class is a NamedTuple that provides the context for the `process` method of a module.

It contains the following attributes:

- `session` (sqlalchemy.ext.asyncio.AsyncSession): The async SQLAlchemy session to be used for database operations.
- `github_project` (github_app_geo_project.configuration.GithubProject): The GitHub project information containing owner, repository, and authenticated GitHub API client (aio_github).
- `github_event_name` (str): The GitHub event name present in the X-GitHub-Event header (e.g., 'push', 'pull_request', 'issues', etc.).
- `github_event_data` (dict[str, Any]): The complete GitHub event data as a dictionary.
- `module_config` (\_CONFIGURATION): The module configuration of the module's type (typically a Pydantic model).
- `module_event_name` (str): The module-specific event name (e.g., 'dashboard', 'cron', etc.).
- `module_event_data` (\_EVENT_DATA): The module event data created by the get_actions method.
- `issue_data` (str): The raw data from the issue dashboard (typically a markdown string).
- `job_id` (int): The unique job ID for this processing task.
- `service_url` (str): The base URL of the application service for generating links and building URLs.

### GithubProject

The `github_app_geo_project.configuration.GithubProject` class provides GitHub project information and authenticated GitHub API client access.

It contains the following attributes:

- `owner` (str): The owner of the GitHub repository.
- `repository` (str): The name of the GitHub repository.
- `aio_github` (githubkit.AsyncGitHub): The authenticated GitHub API client for async operations. This is used to interact with GitHub REST APIs.
- `application` (github_app_geo_project.configuration.GithubApplication): The GitHub application information.

## Future

The project should fully be in async mode.
`pathlib` must not be used.
Adding an `async` to a non-async function is completely possible.
`aiofiles` must not be used, use `anyio` instead.
