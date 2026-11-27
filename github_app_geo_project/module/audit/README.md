A module that does some audit stuff on the project:

- Check for critical vulnerabilities (Snyk)
- Check and fix the Python and npm dependency vulnerabilities (Trivy)
  - Create a pull request for the auto fixable issues
  - Create an issue if the pull request is open for more than 5 days
- Create a pull request for auto fixable issues (Snyk, disabled by default, see `snyk.auto-fix`)
  - Create an issue on error
  - Create an issue if the pull request is open for more than 5 days
- Create a pull request with the updated version in the `ci/dpkg.yaml` files
  - Create an issue if the pull request is open for more than 5 days
- Scan the container images used by the ArgoCD applications (Trivy)
  - Report the list of images of each application and their critical vulnerabilities in the dashboard
- Scan the pull request code for secrets and misconfigurations (Trivy)
  - Report the findings as an action required check on the pull request

The dependency CVEs are checked with Snyk, and the Python and npm dependency CVEs with Trivy, which
also fixes them. The container images used by the ArgoCD applications of the deployment repositories
(`argocd-gs-gmf-apps`, `argocd-gs-ugsp-apps`) are discovered by rendering the Helm charts and
scanned with Trivy. The pull requests are scanned with Trivy, only for the secrets and the
configuration files misconfigurations, not for the dependency vulnerabilities (which are handled
by the Trivy dependencies audit and Snyk on the stabilization branches).

The result will be put in the transversal dashboard.

### Events

This module will be triggered by the `daily` event.

It also reacts to `pull_request` events with action `closed` to close related issues,
and with the actions `opened`, `reopened` and `synchronize` to scan the pull request with Trivy.
When `SECURITY.md` is changed on a `push` to the default branch, it also triggers the same `Renovate` workflow as the `daily` event.

### Job deduplication

To avoid piling up redundant audits, at most one audit job per type and branch can wait in the queue (`new` status) for a given repository: when a new matching job is queued, the previous similar jobs in `new` status are replaced (marked as `skipped`). For example, only one `snyk (1.21)` job at a time; the jobs to close the related issues and the Trivy pull request scans are deduplicated per pull request.

### Job priorities

The lower the priority number, the sooner the job is processed. The priorities are ordered to run the shortest jobs first, to not block the fast jobs behind the slower audit scans:

| Job                             | Priority                | Value |
| ------------------------------- | ----------------------- | ----- |
| `trivy-pull-request (<number>)` | `PRIORITY_STATUS`       | 10    |
| `close-pull-request-issues`     | `PRIORITY_STANDARD`     | 30    |
| `cleanup`                       | `PRIORITY_STANDARD + 1` | 31    |
| `outdated`                      | `PRIORITY_STANDARD + 2` | 32    |
| `renovate`                      | `PRIORITY_STANDARD + 3` | 33    |
| Snyk/dpkg/Trivy fan-out         | `PRIORITY_CRON`         | 40    |
| `renovate (<version>)`          | `PRIORITY_CRON + 1`     | 41    |
| `dpkg (<version>)`              | `PRIORITY_CRON + 2`     | 42    |
| `snyk (<version>)`              | `PRIORITY_CRON + 3`     | 43    |
| `trivy (<branch>)`              | `PRIORITY_CRON + 4`     | 44    |
| `trivy-filesystem (<version>)`  | `PRIORITY_CRON + 5`     | 45    |

The `snyk (<version>)`, `trivy (<branch>)` and `trivy-filesystem (<version>)` jobs are the slowest and
are serialized (`_SNYK_LOCK`, `_TRIVY_LOCK`, shared by the two Trivy jobs), that is why they have
the highest priority numbers. The `trivy-pull-request (<number>)` jobs are serialized by their own
lock too (`_TRIVY_PULL_REQUEST_LOCK`), but they keep the fast `PRIORITY_STATUS` value because they
are pull request checks.

### Other files used by the module

- [`SECURITY.md`](https://github.com/camptocamp/c2cciutils/wiki/SECURITY.md) from the default branch to get the stabilization branches.
- `.tools-version` on the stabilization branch to get the used minor Python version. The matching `pyenv` Python version is lazily installed at runtime if missing.
- `.nvmrc`, `.node-version` or `.tool-versions` (`nodejs` entry, in this order of precedence) on the stabilization branch to get the pinned Node.js version. The matching Node.js version is lazily installed at runtime with `fnm` if missing, and used for `snyk fix` and `npm audit fix`. Without any version file, or if the installation fails, the Node.js version of the Docker image is used.
- `gradlew` on the stabilization branch to get the Gradle version. The Java version used to run Gradle is the one of the `java-path-for-gradle` configuration mapping when it defines the Gradle minor version, otherwise the newest installed OpenJDK compatible with that Gradle version (Gradle 8.x → Java 21, 7.x → Java 17, 6.x → Java 11, looked up in `/usr/lib/jvm`), otherwise the system default Java.
- `.trivyignore` and `.trivyignore.yaml` on the stabilization branch, the ignore policies read natively by the Trivy dependencies scan; the `reason` and `expire` fields of the YAML entries are displayed on the output page.
- `.github/ghci.yaml` on the stabilization branch to get some branch-specific configuration.

### Functionality Details

#### Vulnerability Scanning

The module uses Snyk to scan for vulnerabilities in project dependencies. It focuses on identifying critical security issues that need immediate attention. The scan results are aggregated and reported in the transversal dashboard.

#### Automatic Fix Pull Requests

When Snyk identifies vulnerabilities that can be automatically fixed, the module creates a pull request with the necessary changes. This helps maintain project security by streamlining the remediation process.

The automatic fixes are disabled by default (`snyk.auto-fix` is `false`): the Python and npm
dependency fixes are done by the Trivy dependencies audit (see below), and the fixable
vulnerabilities found by Snyk are only reported. Setting `snyk.auto-fix` to `true` restores the
`snyk fix` and `npm audit fix` commands, the file changes and the pull requests. As for the other
Snyk options, it can be set globally in the `.github/ghci.yaml` file of the default branch, or for
a single stabilization branch in its own `.github/ghci.yaml` file, where it takes precedence.

The pull request body contains:

- A `Fixed vulnerabilities` section listing the vulnerabilities that were fixed by this run, with their severity, package and version, target file, links to the CVE (`nvd.nist.gov`) and to the Snyk vulnerability page, and the version they are fixed in. The list is the difference between the `snyk test --json` scan done before `snyk fix`/`npm audit fix` and the one done after them.
- A `Fixes applied (pins added to requirements files)` section listing the vulnerabilities addressed by a version pin that the fix added to a requirements file, with the same line format plus the pinned version. The rescan cannot confirm these fixes: Snyk builds the pip dependency tree from the packages installed in the audit environment, which is not reinstalled after the fix. The list is derived from the `git diff` taken after the fix commands and matched with the scan done before the fix by target file, package name (normalized as defined by PEP 503) and pinned version; the vulnerabilities already confirmed fixed by the rescan are excluded to not report them twice.
- The raw output of the fix commands (`snyk fix`, `npm audit fix`) in a collapsed `<details>` section.
- A `Logs` link to the job logs and an `Output` link to the Snyk summary report of the branch.

Snyk adds a minimum-version lower bound to the requirements files for every vulnerability it
reports, without checking that the fixed release is installable with the branch interpreter. When
the required Python (`Requires-Python` read from PyPI for the pinned version) is newer than the
branch Python version taken from `.tool-versions`, the pin is dropped from the fix: it would make
the Docker image `pip install` fail deterministically, and such a version is unfixable on that
branch (the vulnerability stays handled by the `.snyk` policy ignores). The dropped pins are
neither committed nor reported as fixes, and are logged with a `Python-incompatible pins skipped`
message.

#### Snyk Summary Report Output

Every `snyk (<version>)` job creates or updates the `/output/<owner>/<repository>/snyk-<version>` output, even when every vulnerability was fixed or when only vulnerabilities below the dashboard threshold remain. The page lists the remaining vulnerabilities, the ignored ones (with the reason found in the `.snyk` policy files) and, collapsed, the low severity ones. It displays `No vulnerability found.` when the three sections are empty. The output URL is stable, it is linked from the pull request body and from the transversal dashboard.

#### Python and npm Dependencies Scan and Fix (Trivy filesystem)

The `trivy-filesystem (<version>)` job scans the Python and npm dependencies of each stabilization
branch with `trivy fs --scanners vuln --format json` and fixes them, which replaces the Snyk auto-fix
for these ecosystems. The scan reads the dependency files (`poetry.lock`, `requirements*.txt`,
`package-lock.json`) directly: the dependencies are not installed in the audit environment, and the
rescan done after the fixes is therefore a real confirmation of what was fixed, unlike the Snyk
rescan which could not confirm the pins added to the requirements files. Every vulnerability from
the LOW severity is reported; only
those at or above `trivy.filesystem.fix-severity-threshold` (default `high`) are fixed, to limit the
churn of the transitive fixes, and those at or above `trivy.filesystem.dashboard-severity-threshold`
(default `high`) are listed directly on the transversal dashboard, the others being displayed in a
collapsed section of the output page.

The project type is auto-detected per directory:

- **With Poetry** (a `poetry.lock` file is committed next to its `pyproject.toml`): the lock is
  bumped to the smallest version fixing all the selected vulnerabilities of the package, direct or
  transitive, without touching the `pyproject.toml`. For each package a temporary `==<fixed>`
  constraint is written in the `pyproject.toml`, `poetry lock` (conservative in Poetry 2.x,
  `--no-update` on 1.x) moves only that entry, the original `pyproject.toml` is restored and
  `poetry lock` is run again, validated with `poetry check --lock`. A fixed version forbidden by the
  original constraint of a direct dependency, or that does not resolve with the other constraints,
  is not pinned: it is reported as a skipped fix (the direct dependency updates are the job of
  Renovate).
- **Without Poetry** (requirements files): an existing `==`/`>=` pin is bumped to the fixed version,
  a vulnerable package that is not listed gets a `<package>>=<fixed>` floor appended, then the edited
  file is validated with `pip install --dry-run --ignore-installed` (extra arguments from
  `trivy.filesystem.pip-install-arguments`); a file that does not resolve anymore is fully reverted and
  reported as a skipped fix.
- **npm** (a `package-lock.json` file): a single `npm audit fix --package-lock-only --ignore-scripts`
  run per lock file fixes all the selected packages of the project, with the Node.js version pinned by
  the repository (`.nvmrc`, `.node-version`, `.tool-versions`) installed on demand with
  [fnm](https://github.com/Schniz/fnm). No `node_modules` is installed and no lifecycle script is
  run, the rescan confirms what was really fixed in the lock file. Range prefixes added by npm to
  exact `package.json` pins are stripped back; packages with no fix inside the current ranges are not
  forced: they are reported as a skipped fix (the direct dependency updates are the job of Renovate).

The Python modes check the fixed releases against the branch Python version (`.tool-versions`):
a version whose PyPI `Requires-Python` is newer than the branch interpreter is never pinned, the
vulnerability stays handled by the ignore policies. Pipenv (`Pipfile.lock`), Yarn and pnpm, and the
other ecosystems are reported, not fixed.

The fixes are pushed to the `ghci/audit/trivy-filesystem/<version>` branch with auto-merge enabled, and
the pull request body contains the `Fixed vulnerabilities` section (confirmed by the rescan), the
`Fixes applied (pins added to requirements files)` section, the `Fixes not applied` section with the
reason of each skipped fix, the raw fix output and the `Logs`/`Output` links. The ignored
vulnerabilities are displayed with the reason read from the `.trivyignore.yaml` files; note that the
Snyk ignores (`.snyk` files) do not apply to this scan, they must be duplicated into a
`.trivyignore.yaml` file (with `id`, `reason` and optionally `expire`).

Every `trivy-filesystem (<version>)` job creates or updates the
`/output/<owner>/<repository>/trivy-filesystem-<version>` page, listing the remaining and the ignored
vulnerabilities with their CVE links, and the transversal dashboard gets a
`Trivy filesystem check/fix <version>` entry. When `trivy.filesystem.auto-fix` is set to `false`, the
vulnerabilities are only reported and no pull request is created.

#### Container Images Scan (Trivy)

The ArgoCD deployment repositories (`argocd-gs-gmf-apps`, `argocd-gs-ugsp-apps`) reference a large
number of container images that are not visible from a plain dependency scan. The `audit` module
discovers and scans them with [Trivy](https://trivy.dev), replacing the legacy `snyk-images`
GitHub Actions workflow (whose Snyk token, fetched from the abandoned gopass store, no longer
works). The whole feature runs **inside the GHCI worker pods**:

- The default branch of the repository is cloned into the git worktree cache, no ArgoCD server
  or Kubernetes cluster access is needed.
- The applications are discovered from the `apps/<environment>/[<client>/]<app>/Chart.yaml` tree,
  limited to the configured `environments` (default `["int", "prod"]`, which excludes the
  `apps/example/` test applications). The directory layout (`single` for
  `apps/<env>/<app>/`, `two-level` for `apps/<env>/<client>/<app>/`) is **detected automatically**
  with `app-structure: auto` (the default), so no per-repository configuration is required;
  `excluded-apps` adds optional regex exclusions.
- Each application directory is rendered with `helm dependency update` and `helm template`; the
  container image references are extracted from the generated manifests
  (both `image: <name>:<tag>` strings and `image: {repository, tag|digest}` objects). The
  `helm dependency update` is run without `--skip-refresh` because the subchart repositories are
  not registered with `helm repo add` and, with that flag, Helm fails to resolve them (it looks
  for an index cache of the ad hoc `helm-manager-<hash>` repository that is never written), see
  [helm/helm#11001](https://github.com/helm/helm/issues/11001) and
  [helm/helm#12921](https://github.com/helm/helm/issues/12921).
- Every distinct image reference is scanned exactly once with
  `trivy image --format=json --severity=<SEVERITY> <image>`, with a semaphore of 4 concurrent
  scans and a module-wide `_TRIVY_LOCK` so that two scans never overlap. An image shared by
  several applications is therefore **not scanned twice**.
- The Trivy database cache is written to `/var/www/.cache/trivy` (the same writable directory
  pattern as `/opt/fnm/node-versions`), the container entrypoint does not need any extra
  `emptyDir` mount.

Every `trivy (<branch>)` job creates or updates the
`/output/<owner>/<repository>/trivy-images-<branch>` page, listing per application the container
images it uses, with for each image the reported vulnerabilities (CVE, package, installed
version, fixed version, title). The transversal dashboard gets a `Trivy images <branch>` entry
summarizing the scan (`N applications, M images, K critical`) with `Logs` and `Output` links.

The image scan is enabled by default (`audit.trivy.image.enabled`) with `app-structure: auto`, so an
ArgoCD deployment repository needs **no configuration**: the fan-out spawns a `trivy` job as soon
as the default branch has an `apps` directory and a scanned `environment`. Repositories without
ArgoCD applications are therefore unaffected.

The fan-out also handles the case of a repository **without `SECURITY.md`** (both argocd
repositories): instead of pruning all transversal entries, it keeps the `Trivy images <branch>`
entries in `known_types` and continues the daily cleanup, so that a scan result is not erased
every day.

#### Pull Request Scan (Trivy)

Every `opened`, `reopened` or `synchronize` pull request event spawns a
`trivy-pull-request (<number>)` job that scans the head of the pull request in the worker:

- The head is fetched with the `refs/pull/<number>/head` ref of the repository cache, which
  also covers the pull requests opened from a fork.
- `trivy fs --scanners=<scanners> --severity=<severity> --format=json .` scans the whole tree
  for the configured scanners (`secret` and `misconfig` by default).
- `trivy config <file> --severity=<severity> --format=json` runs on each of the
  `config-targets` files (`Dockerfile` by default) that exists in the pull request.
- The `--ignorefile` argument is added with the configured `ignore-file` path
  (`.github/.trivyignore.yaml` by default, in `audit.trivy.pull-request`) only if the file
  exists in the pull request.
- The scan is serialized by `_TRIVY_PULL_REQUEST_LOCK` and uses the same Trivy database cache
  as the container images scans (`/var/www/.cache/trivy`).

The findings do not fail the pull request: the check run is completed with the
`action_required` conclusion (orange warning) and lists up to 50 findings per command
(severity, rule, file, line, title) in its summary. The check run is red only when Trivy
itself failed to run (tool error or timeout), and green when there is no finding. The scan is
enabled by default; set `audit.trivy.pull-request.enabled` to `false` in `.github/ghci.yaml`
to disable it (the check run then succeeds with a `disabled` message).

Add a `.github/.trivyignore.yaml` file in the repository to ignore known false positives, for
example:

```yaml
ignores:
  - ids:
      - generic-api-key
    paths:
      - tests/**
```

The check is advisory: required status checks should not be configured on it, it is meant to
attract attention on new secrets or Dockerfile misconfigurations.

#### Version Update Pull Requests

For projects using the `ci/dpkg.yaml` file format, the module checks for outdated dependencies and creates pull requests with updated versions. This keeps dependencies up-to-date and reduces technical debt.

#### Issue Management

If errors occur during the scanning or PR creation process, or if pull requests remain open for too long (> 5 days), the module creates issues to alert the project maintainers.

#### Cleanup and Clean Situation Report

The daily `cleanup` job (also triggered when `SECURITY.md` is removed from the default branch) removes the leftovers of versions that are no more supported:

- Close the `ghci/audit/{snyk,dpkg,renovate,trivy-filesystem}/<version>` branches and their pull requests, including the branches left behind by merged or manually closed pull requests.
- Close the related bot issues (`Pull request Audit ... is open for N days`), including the `Cleanup Renovate configuration` ones.
- Delete the persisted Snyk outputs (`/output/<owner>/<repository>/snyk-<version>`), the Trivy images outputs (`/output/<owner>/<repository>/trivy-images-<branch>`) and the Trivy dependencies outputs (`/output/<owner>/<repository>/trivy-filesystem-<version>`) of the removed versions.
- Clear the dashboard checks when `SECURITY.md` is removed.

The Snyk/dpkg/Trivy fan-out job additionally prunes the transversal dashboard entries (`Snyk check/fix <version>`, `Dpkg <version>`, `Trivy filesystem check/fix <version>`, `Trivy images <branch>`) and the legacy vulnerability sections (`=== <version>` in the dashboard issue) of the versions removed from `SECURITY.md`.

The `cleanup` job reports the resulting situation in the output of its check run (`Cleanup: Everything is clean`, or `Cleanup: N leftover(s) removed` with the details of what was removed) and as a `Cleanup` entry in the transversal dashboard.

#### Snyk cloud cleanup

When the Snyk REST API is configured (`snyk_token`/`SNYK_TOKEN` and optionally `snyk_org`/`SNYK_ORG`, kill switch `GHCI__AUDIT__SNYK_API_CLEANUP`), the module also cleans the Snyk cloud side, which is structured as Target → Reference → Project:

- After each successful `snyk monitor`, the projects of the branch reference that were not refreshed by the run (dependency files that are not scanned anymore) are deleted. A reference always contains exactly the current dependency files, without recreating the projects every day.
- The daily `cleanup` job deletes all the projects whose `target_reference` is a version that is not in `SECURITY.md` anymore (all the references of the repository when `SECURITY.md` is removed). A reference that does not contain any project anymore disappears from the Snyk UI.
- The daily `cleanup` job also deletes the projects that were not re-monitored since too long (`snyk_api_stale_age` setting, default 7 days), independently of the monitor runs result. This removes the leftovers of the runs that created projects with unstable names, and the projects of references whose monitor fails for a long time.

When no organization is configured, the single organization accessible with the API token is used, otherwise a warning is logged and the cleanup is skipped.

Only the projects with the `cli` origin are deleted. The cleanup is best-effort: API errors never fail the audit job, but they are reported — the `cleanup` job check run summary and its `Cleanup` transversal dashboard entry display `Snyk API error(s)` (with the `error` status), and the post-monitor pruning adds a `Snyk projects cleanup failed` message to the job output. Without a token the cleanup is skipped, and the skip reasons are logged at the `INFO` level. The removed references and stale projects are part of the `cleanup` job clean situation report.

### Configuration Options

You can configure the audit module behavior through the `.github/ghci.yaml` file.

[Configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/AUDIT-CONFIG.md).

#### Transversal Dashboard

The vulnerability dashboard is displayed on the web UI at `/dashboard/audit` (the transversal dashboard).

Each check entry displays a plain text summary and links to the job `Logs` and to the `Output` report; when the audit job created a pull request, a `Pull request` link is displayed as well.

You can control which vulnerabilities appear on the dashboard with:

- `snyk.dashboard-severity-threshold` — minimum severity level to display on the transversal dashboard (default: `medium`)
- `snyk.excluded-files` — list of regex patterns for file names to exclude from the transversal dashboard
- `trivy.filesystem.dashboard-severity-threshold` — same, for the Trivy dependencies entries (default: `high`)
- `trivy.filesystem.excluded-files` — same, for the Trivy dependencies entries
