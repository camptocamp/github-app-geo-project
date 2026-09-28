A module that does some audit stuff on the project:

- Check for critical vulnerabilities (Snyk)
- Create a pull request for auto fixable issues (Snyk)
  - Create an issue on error
  - Create an issue if the pull request is open for more than 5 days
- Create a pull request with the updated version in the `ci/dpkg.yaml` files
  - Create an issue if the pull request is open for more than 5 days

Currently, the module checks the CVEs on the dependencies, but it does not check the code neither the generated Docker images.

The result will be put in the transversal dashboard.

### Events

This module will be triggered by the `daily` event.

It also reacts to `pull_request` events with action `closed` to close related issues.
When `SECURITY.md` is changed on a `push` to the default branch, it also triggers the same `Renovate` workflow as the `daily` event.

### Job deduplication

To avoid piling up redundant audits, at most one audit job per type and branch can wait in the queue (`new` status) for a given repository: when a new matching job is queued, the previous similar jobs in `new` status are replaced (marked as `skipped`). For example, only one `snyk (1.21)` job at a time; the jobs to close the related issues are deduplicated per pull request.

### Job priorities

The lower the priority number, the sooner the job is processed. The priorities are ordered to run the shortest jobs first, to not block the fast jobs behind the slower audit scans:

| Job                         | Priority                | Value |
| --------------------------- | ----------------------- | ----- |
| `close-pull-request-issues` | `PRIORITY_STANDARD`     | 30    |
| `cleanup`                   | `PRIORITY_STANDARD + 1` | 31    |
| `outdated`                  | `PRIORITY_STANDARD + 2` | 32    |
| `renovate`                  | `PRIORITY_STANDARD + 3` | 33    |
| Snyk/dpkg fan-out           | `PRIORITY_CRON`         | 40    |
| `renovate (<version>)`      | `PRIORITY_CRON + 1`     | 41    |
| `dpkg (<version>)`          | `PRIORITY_CRON + 2`     | 42    |
| `snyk (<version>)`          | `PRIORITY_CRON + 3`     | 43    |

The `snyk (<version>)` jobs are the slowest and are serialized (`_SNYK_LOCK`), that is why they have the highest priority number.

### Other files used by the module

- [`SECURITY.md`](https://github.com/camptocamp/c2cciutils/wiki/SECURITY.md) from the default branch to get the stabilization branches.
- `.tools-version` on the stabilization branch to get the used minor Python version. The matching `pyenv` Python version is lazily installed at runtime if missing.
- `.nvmrc`, `.node-version` or `.tool-versions` (`nodejs` entry, in this order of precedence) on the stabilization branch to get the pinned Node.js version. The matching Node.js version is lazily installed at runtime with `fnm` if missing, and used for `snyk fix` and `npm audit fix`. Without any version file, or if the installation fails, the Node.js version of the Docker image is used.
- `.github/ghci.yaml` on the stabilization branch to get some branch-specific configuration.

### Functionality Details

#### Vulnerability Scanning

The module uses Snyk to scan for vulnerabilities in project dependencies. It focuses on identifying critical security issues that need immediate attention. The scan results are aggregated and reported in the transversal dashboard.

#### Automatic Fix Pull Requests

When Snyk identifies vulnerabilities that can be automatically fixed, the module creates a pull request with the necessary changes. This helps maintain project security by streamlining the remediation process.

#### Version Update Pull Requests

For projects using the `ci/dpkg.yaml` file format, the module checks for outdated dependencies and creates pull requests with updated versions. This keeps dependencies up-to-date and reduces technical debt.

#### Issue Management

If errors occur during the scanning or PR creation process, or if pull requests remain open for too long (> 5 days), the module creates issues to alert the project maintainers.

#### Cleanup and Clean Situation Report

The daily `cleanup` job (also triggered when `SECURITY.md` is removed from the default branch) removes the leftovers of versions that are no more supported:

- Close the `ghci/audit/{snyk,dpkg,renovate}/<version>` branches and their pull requests, including the branches left behind by merged or manually closed pull requests.
- Close the related bot issues (`Pull request Audit ... is open for N days`), including the `Cleanup Renovate configuration` ones.
- Delete the persisted Snyk outputs (`/output/<owner>/<repository>/snyk-<version>`) of the removed versions.
- Clear the dashboard checks when `SECURITY.md` is removed.

The Snyk/dpkg fan-out job additionally prunes the transversal dashboard entries (`Snyk check/fix <version>`, `Dpkg <version>`) and the legacy vulnerability sections (`=== <version>` in the dashboard issue) of the versions removed from `SECURITY.md`.

The `cleanup` job reports the resulting situation in the output of its check run (`Cleanup: Everything is clean`, or `Cleanup: N leftover(s) removed` with the details of what was removed) and as a `Cleanup` entry in the transversal dashboard.

#### Snyk cloud cleanup

When the Snyk REST API is configured (`snyk_token`/`SNYK_TOKEN` and `snyk_org`/`SNYK_ORG`, kill switch `GHCI__AUDIT__SNYK_API_CLEANUP`), the module also cleans the Snyk cloud side, which is structured as Target → Reference → Project:

- After each successful `snyk monitor`, the projects of the branch reference that were not refreshed by the run (dependency files that are not scanned anymore) are deleted. A reference always contains exactly the current dependency files, without recreating the projects every day.
- The daily `cleanup` job deletes all the projects whose `target_reference` is a version that is not in `SECURITY.md` anymore (all the references of the repository when `SECURITY.md` is removed). A reference that does not contain any project anymore disappears from the Snyk UI.

Only the projects with the `cli` origin are deleted. The cleanup is best-effort: API errors are logged and never fail the audit job, and without a token or organization configured it is silently skipped. The removed references are part of the `cleanup` job clean situation report.

### Configuration Options

You can configure the audit module behavior through the `.github/ghci.yaml` file.

[Configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/AUDIT-CONFIG.md).

#### Transversal Dashboard

The vulnerability dashboard is displayed on the web UI at `/dashboard/audit` (the transversal dashboard).

You can control which vulnerabilities appear on the dashboard with:

- `snyk.dashboard-severity-threshold` — minimum severity level to display on the transversal dashboard (default: `medium`)
- `snyk.excluded-files` — list of regex patterns for file names to exclude from the transversal dashboard

#### Security Advisories

When vulnerabilities with a severity at or above the configurable threshold are detected, the module automatically creates GitHub Security Advisories. This feature requires the `security_advisories: write` permission.

You can control advisory creation with:

- `snyk.advisory-severity-threshold` — minimum severity level to create a Security Advisory (default: `high`)
- `snyk.excluded-files` — list of regex patterns for file names to exclude from advisory creation
