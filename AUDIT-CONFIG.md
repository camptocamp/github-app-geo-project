# Audit modules configuration

## Properties

- **`audit`** _(object)_: Cannot contain additional properties.
  - **`snyk`** _(object)_: Cannot contain additional properties.
    - **`enabled`** _(boolean)_: Enable Snyk audit. Default: `true`.
    - **`files-no-install`** _(array)_: Dependency files that will not be installed. Default: `[]`.
      - **Items** _(string)_
    - **`pip-install-arguments`** _(array)_: Arguments to pass to pip install. Default: `["--user"]`.
      - **Items** _(string)_
    - **`pipenv-sync-arguments`** _(array)_: Arguments to pass to pipenv sync. Default: `[]`.
      - **Items** _(string)_
    - **`monitor-arguments`** _(array)_: Arguments to pass to Snyk monitor. Default: `["--all-projects"]`.
      - **Items** _(string)_
    - **`test-arguments`** _(array)_: Arguments to pass to Snyk test. Default: `["--all-projects", "--severity-threshold=medium"]`.
      - **Items** _(string)_
    - **`fix-arguments`** _(array)_: Arguments to pass to Snyk fix. Default: `["--all-projects"]`.
      - **Items** _(string)_
  - **`dpkg`** _(object)_: Cannot contain additional properties.
    - **`enabled`** _(boolean)_: Enable dpkg audit. Default: `true`.
    - **`sources`** _(object)_: Can contain additional properties. Default: `{"ubuntu_22_04": [{"url": "http://archive.ubuntu.com/ubuntu", "distribution": "jammy", "components": ["main", "restricted", "universe", "multiverse"]}, {"url": "http://security.ubuntu.com/ubuntu", "distribution": "jammy-security", "components": ["main", "restricted", "universe", "multiverse"]}, {"url": "http://security.ubuntu.com/ubuntu", "distribution": "jammy-updates", "components": ["main", "restricted", "universe", "multiverse"]}], "ubuntu_24_04": [{"url": "http://archive.ubuntu.com/ubuntu", "distribution": "noble", "components": ["main", "restricted", "universe", "multiverse"]}, {"url": "http://security.ubuntu.com/ubuntu", "distribution": "noble-security", "components": ["main", "restricted", "universe", "multiverse"]}, {"url": "http://security.ubuntu.com/ubuntu", "distribution": "noble-updates", "components": ["main", "restricted", "universe", "multiverse"]}], "debian_11": [{"url": "http://deb.debian.org/debian", "distribution": "bullseye", "components": ["main", "contrib", "non-free"]}, {"url": "http://deb.debian.org/debian", "distribution": "bullseye-updates", "components": ["main", "contrib", "non-free"]}, {"url": "http://security.debian.org/debian-security", "distribution": "bullseye-security", "components": ["main", "contrib", "non-free"]}], "debian_12": [{"url": "http://deb.debian.org/debian", "distribution": "bookworm", "components": ["main", "contrib", "non-free"]}, {"url": "http://deb.debian.org/debian", "distribution": "bookworm-updates", "components": ["main", "contrib", "non-free"]}, {"url": "http://security.debian.org/debian-security", "distribution": "bookworm-security", "components": ["main", "contrib", "non-free"]}]}`.
      - **Additional properties** _(array)_
        - **Items** _(object)_
          - **`url`** _(string)_: URL of the source.
          - **`distribution`** _(string)_: Distribution of the source.
          - **`components`** _(array)_: Components of the source.
            - **Items** _(string)_
