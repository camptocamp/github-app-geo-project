`job.github_event_data` exists, don't replace it by `job.github.event_data`.
These properties also exists don't replace them:

- `job.github_event_name`,
- `job.github_event_data`,
- `context.module_event_data`,
- `context.github_event_data`,
- ...

These properties don't exist, don't use them:

- `job.github`
- `context.github`
- `context.module`
- `context.module_event`

## Modules

the modules are stored in `github_app_geo_project/module/<name>/`.

If needed the module has a JSON schema configuration schema stored in `github_app_geo_project/module/<name>/schema.json`.

Each modules should have a complete end user documentation in a markdown file usually `github_app_geo_project/module/<name>/README.md`.

The modules should be reasonably tested in `tests/test_module_<name>.py`.

## Future

The project should fully be in async mode.
`pathlib` must not be used.
Adding an `async` to a non-async function is completely possible.
`aiofiles` must not be used, use `anyio` instead.
