# Audit modules configuration

## Properties

- **`audit`** _(object)_: Cannot contain additional properties.
  - **`files-no-install`** _(array)_: Dependency files that will not be installed. Default: `[]`.
    - **Items** _(string)_
  - **`pip-install-arguments`** _(array)_: Arguments to pass to pip install. Default: `["--user"]`.
    - **Items** _(string)_
  - **`pipenv-sync-arguments`** _(array)_: Arguments to pass to pipenv sync. Default: `[]`.
    - **Items** _(string)_
  - **`monitor-arguments`** _(array)_: Arguments to pass to snyk monitor. Default: `["--all-projects"]`.
    - **Items** _(string)_
  - **`test-arguments`** _(array)_: Arguments to pass to snyk test. Default: `["--all-projects", "--fail-on=upgradable", "--severity-threshold=medium"]`.
    - **Items** _(string)_
  - **`fix-arguments`** _(array)_: Arguments to pass to snyk fix. Default: `["--all-projects"]`.
    - **Items** _(string)_
