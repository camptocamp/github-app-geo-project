Module that collects the dependencies used by a module and displays the internal and reverse dependencies in the dashboard.

It uses the [`SECURITY.md`](https://github.com/camptocamp/c2cciutils/wiki/SECURITY.md) from the default branch to get the stabilization branches.

It uses [Renovate](https://github.com/camptocamp/c2cciutils/wiki/Renovate-integration) to find the dependencies.

It also uses the [`ci/config.yaml`](https://github.com/camptocamp/c2cciutils/blob/master/config.md) to get to published Docker images and tag.

And also read the standard `package.json`, `pyproject.toml` and `setup.py` to get the packages names.

[Configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/VERSIONS-CONFIG.md).
