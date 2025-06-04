A module that does some audit stuff on the project:

- Check for critical vulnerabilities (Snyk)
- Create a pull request for auto fixable issues (Snyk)
  - Create an issue on error
  - Create an issue if the pull request is open for more than 5 days
- Create a pull request with the updated version in the `ci/dpkg.yaml` files
  - Create an issue if the pull request is open for more than 5 days

Currently, the module checks the CVEs on the dependencies, but it does not check the code neither the generated Docker images.

The result will be put in the dashboard issue.

### Events

This module will be triggered by the `daily` event.

### Other files used by the module

- [`SECURITY.md`](https://github.com/camptocamp/c2cciutils/wiki/SECURITY.md) from the default branch to get the stabilization branches.
- `.tools-version` on the stabilization branch to get the used minor Python version.
- `.github/ghci.yaml` on the stabilization branch to get some branch-specific configuration.

[Configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/AUDIT-CONFIG.md).
