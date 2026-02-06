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

### Functionality Details

#### Vulnerability Scanning

The module uses Snyk to scan for vulnerabilities in project dependencies. It focuses on identifying critical security issues that need immediate attention. The scan results are aggregated and reported in the dashboard issue.

#### Automatic Fix Pull Requests

When Snyk identifies vulnerabilities that can be automatically fixed, the module creates a pull request with the necessary changes. This helps maintain project security by streamlining the remediation process.

#### Version Update Pull Requests

For projects using the `ci/dpkg.yaml` file format, the module checks for outdated dependencies and creates pull requests with updated versions. This keeps dependencies up-to-date and reduces technical debt.

#### Issue Management

If errors occur during the scanning or PR creation process, or if pull requests remain open for too long (> 5 days), the module creates issues to alert the project maintainers.

### Configuration Options

You can configure the audit module behavior through the `.github/ghci.yaml` file.

[Configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/AUDIT-CONFIG.md).
