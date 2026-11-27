Scan the pull request code with [Trivy](https://trivy.dev) for secrets and
misconfigurations, and report the findings as a warning on the pull request.

### Functionality Details

For every pull request, the module checks out the head of the pull request in the
worker (the `refs/pull/<number>/head` ref, which also covers pull requests opened
from a fork) and runs:

- `trivy fs --scanners=<scanners> --severity=<severity> --format=json .`, scanning the
  whole tree for the configured scanners (secrets and misconfigurations by default).
- `trivy config <file> --severity=<severity> --format=json`, for each of the
  `config-targets` files that exist in the repository (`Dockerfile` by default).

The `--ignorefile` argument is added with the configured `ignore-file` path
(`.github/.trivyignore.yaml` by default) only if the file exists in the pull request.

The check is created with the `action_required` conclusion (orange ❗) when Trivy
findings are reported: it warns without failing the pull request checks. The check is
red only if Trivy itself failed to run (tool error or timeout), and green when there is
no finding. The findings (severity, rule, file, line, title) are listed in the check
summary, up to 50 per command.

### When is it triggered?

On the `opened`, `reopened` and `synchronize` pull request events. Queued scans of the
same pull request are replaced by the newest one, so fast pushes do not accumulate scans.

### Configuration Options

You can configure the module using the `.github/ghci.yaml` file, under the
`pull-request-trivy` key; use `enabled: false` to disable it for a repository.

[Configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/PULL-REQUEST-TRIVY-CONFIG.md).

### Usage Notes

- Add a `.github/.trivyignore.yaml` file in the repository to ignore known false
  positives, for example:

  ```yaml
  ignores:
    - ids:
        - generic-api-key
      paths:
        - tests/**
  ```

- The check is advisory: required status checks should not be configured on it, it is
  meant to attract attention on new secrets or Dockerfile misconfigurations.
