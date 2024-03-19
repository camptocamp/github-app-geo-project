# Audit modules configuration

## Properties

- **`audit`** _(object)_: Cannot contain additional properties.
  - **`files-no-install`** _(array)_: Dependency files that will not be installed.
    - **Items** _(string)_
  - **`pip-install-arguments`** _(array)_: Arguments to pass to pip install.
    - **Items** _(string)_
  - **`pipenv-sync-arguments`** _(array)_: Arguments to pass to pipenv sync.
    - **Items** _(string)_
  - **`monitor-arguments`** _(array)_: Arguments to pass to snyk monitor.
    - **Items** _(string)_
  - **`test-arguments`** _(array)_: Arguments to pass to snyk test.
    - **Items** _(string)_
  - **`fix-arguments`** _(array)_: Arguments to pass to snyk fix.
    - **Items** _(string)_
