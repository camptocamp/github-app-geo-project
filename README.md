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

[project configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/github_app_geo_project/PROJECT-CONFIG.md).

[application configuration wiki](https://github.com/camptocamp/github-app-geo-project/wiki/Application-configuration).

[application configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/github_app_geo_project/APPLICATION-CONFIG.md).

## Contributing

Install the pre-commit hooks:

```bash
pip install pre-commit
pre-commit install --allow-missing-config
```

The `prospector` tests should pass.

The code should be typed.

The code should be tested with `pytests`.
