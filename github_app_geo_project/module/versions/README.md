Module that collects the dependencies used by a module and displays the internal and reverse dependencies in the dashboard.

### Functionality Details

This module provides insights into the dependencies used by a project and their relationships. It:

- **Dependency Collection**: Gathers information about dependencies from standard files like `package.json`, `pyproject.toml`, and `setup.py`.
- **Reverse Dependency Analysis**: Identifies other modules or projects that depend on the current module.
- **Dashboard Integration**: Displays collected data in a centralized dashboard for easy visualization.
- **Docker Image and Tag Analysis**: Uses the [`ci/config.yaml`](https://github.com/camptocamp/c2cciutils/blob/master/config.md) file to analyze published Docker images and tags.
- [**Renovate Integration**](https://github.com/camptocamp/c2cciutils/wiki/Renovate-integration): Leverages Renovate to find and manage dependencies.

### Configuration Options

You can configure the versions module using the `.github/ghci.yaml` file.

### Usage Notes

- Ensure the [`SECURITY.md`](https://github.com/camptocamp/c2cciutils/wiki/SECURITY.md) file from default branch is up-to-date to accurately reflect stabilization branches.
- Use Renovate to automate dependency updates and reduce maintenance overhead.
- Regularly review the dashboard to identify outdated or vulnerable dependencies.

[Configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/VERSIONS-CONFIG.md).
