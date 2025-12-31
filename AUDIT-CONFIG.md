# Audit modules configuration

## Properties

- <a id="properties/audit"></a>**`audit`** *(object)*: Cannot contain additional properties.
  - <a id="properties/audit/properties/enabled"></a>**`enabled`** *(boolean)*: Default: `true`.
  - <a id="properties/audit/properties/snyk"></a>**`snyk`** *(object)*: Cannot contain additional properties.
    - <a id="properties/audit/properties/snyk/properties/enabled"></a>**`enabled`** *(boolean)*: Enable Snyk audit. Default: `true`.
    - <a id="properties/audit/properties/snyk/properties/files-no-install"></a>**`files-no-install`** *(array)*: Dependency files that will not be installed. Default: `[]`.
      - <a id="properties/audit/properties/snyk/properties/files-no-install/items"></a>**Items** *(string)*
    - <a id="properties/audit/properties/snyk/properties/pip-install-arguments"></a>**`pip-install-arguments`** *(array)*: Arguments to pass to pip install. Default: `[]`.
      - <a id="properties/audit/properties/snyk/properties/pip-install-arguments/items"></a>**Items** *(string)*
    - <a id="properties/audit/properties/snyk/properties/pipenv-sync-arguments"></a>**`pipenv-sync-arguments`** *(array)*: Arguments to pass to pipenv sync. Default: `[]`.
      - <a id="properties/audit/properties/snyk/properties/pipenv-sync-arguments/items"></a>**Items** *(string)*
    - <a id="properties/audit/properties/snyk/properties/poetry-install-arguments"></a>**`poetry-install-arguments`** *(array)*: Arguments to pass to pip install. Default: `[]`.
      - <a id="properties/audit/properties/snyk/properties/poetry-install-arguments/items"></a>**Items** *(string)*
    - <a id="properties/audit/properties/snyk/properties/java-path-for-gradle"></a>**`java-path-for-gradle`** *(object)*: Path to the directory that contains Java executable to use for the Gradle minor version. Can contain additional properties. Default: `{}`.
      - <a id="properties/audit/properties/snyk/properties/java-path-for-gradle/additionalProperties"></a>**Additional properties** *(string)*
    - <a id="properties/audit/properties/snyk/properties/monitor-arguments"></a>**`monitor-arguments`** *(array)*: Arguments to pass to Snyk monitor. Default: `["--all-projects"]`.
      - <a id="properties/audit/properties/snyk/properties/monitor-arguments/items"></a>**Items** *(string)*
    - <a id="properties/audit/properties/snyk/properties/test-arguments"></a>**`test-arguments`** *(array)*: Arguments to pass to Snyk test. Default: `["--all-projects", "--severity-threshold=medium"]`.
      - <a id="properties/audit/properties/snyk/properties/test-arguments/items"></a>**Items** *(string)*
    - <a id="properties/audit/properties/snyk/properties/fix-arguments"></a>**`fix-arguments`** *(array)*: Arguments to pass to Snyk fix. Default: `["--all-projects"]`.
      - <a id="properties/audit/properties/snyk/properties/fix-arguments/items"></a>**Items** *(string)*
    - <a id="properties/audit/properties/snyk/properties/monitor"></a>**`monitor`** *(object)*: Cannot contain additional properties.
      - <a id="properties/audit/properties/snyk/properties/monitor/properties/project-environment"></a>**`project-environment`** *(array)*: Set the project environment project attribute. To clear the project environment set empty array.
For more information see Project attributes https://docs.snyk.io/getting-started/introduction-to-snyk-projects/view-project-information/project-attributes.
        - <a id="properties/audit/properties/snyk/properties/monitor/properties/project-environment/items"></a>**Items** *(string)*: Must be one of: "frontend", "backend", "internal", "external", "mobile", "saas", "onprem", "hosted", or "distributed".
      - <a id="properties/audit/properties/snyk/properties/monitor/properties/project-lifecycle"></a>**`project-lifecycle`** *(array)*: Set the project lifecycle project attribute. To clear the project lifecycle set empty array.
For more information see Project attributes https://docs.snyk.io/snyk-admin/snyk-projects/project-tags.
        - <a id="properties/audit/properties/snyk/properties/monitor/properties/project-lifecycle/items"></a>**Items** *(string)*: Must be one of: "production", "development", or "sandbox".
      - <a id="properties/audit/properties/snyk/properties/monitor/properties/project-business-criticality"></a>**`project-business-criticality`** *(array)*: Set the project business criticality project attribute. To clear the project business criticality set empty array.
For more information see Project attributes https://docs.snyk.io/snyk-admin/snyk-projects/project-tags.
        - <a id="properties/audit/properties/snyk/properties/monitor/properties/project-business-criticality/items"></a>**Items** *(string)*: Must be one of: "critical", "high", "medium", or "low".
      - <a id="properties/audit/properties/snyk/properties/monitor/properties/project-tags"></a>**`project-tags`** *(object)*: Set the project tags to one or more values.
To clear the project tags set empty dictionary. Can contain additional properties.
        - <a id="properties/audit/properties/snyk/properties/monitor/properties/project-tags/additionalProperties"></a>**Additional properties** *(string)*
  - <a id="properties/audit/properties/dpkg"></a>**`dpkg`** *(object)*: Cannot contain additional properties.
    - <a id="properties/audit/properties/dpkg/properties/enabled"></a>**`enabled`** *(boolean)*: Enable dpkg audit. Default: `true`.
    - <a id="properties/audit/properties/dpkg/properties/sources"></a>**`sources`** *(object)*: Can contain additional properties. Default: `{"ubuntu_22_04": [{"url": "http://archive.ubuntu.com/ubuntu", "distribution": "jammy", "components": ["main", "restricted", "universe", "multiverse"]}, {"url": "http://security.ubuntu.com/ubuntu", "distribution": "jammy-security", "components": ["main", "restricted", "universe", "multiverse"]}, {"url": "http://security.ubuntu.com/ubuntu", "distribution": "jammy-updates", "components": ["main", "restricted", "universe", "multiverse"]}], "ubuntu_24_04": [{"url": "http://archive.ubuntu.com/ubuntu", "distribution": "noble", "components": ["main", "restricted", "universe", "multiverse"]}, {"url": "http://security.ubuntu.com/ubuntu", "distribution": "noble-security", "components": ["main", "restricted", "universe", "multiverse"]}, {"url": "http://security.ubuntu.com/ubuntu", "distribution": "noble-updates", "components": ["main", "restricted", "universe", "multiverse"]}], "debian_11": [{"url": "http://deb.debian.org/debian", "distribution": "bullseye", "components": ["main", "contrib", "non-free"]}, {"url": "http://deb.debian.org/debian", "distribution": "bullseye-updates", "components": ["main", "contrib", "non-free"]}, {"url": "http://security.debian.org/debian-security", "distribution": "bullseye-security", "components": ["main", "contrib", "non-free"]}], "debian_12": [{"url": "http://deb.debian.org/debian", "distribution": "bookworm", "components": ["main", "contrib", "non-free"]}, {"url": "http://deb.debian.org/debian", "distribution": "bookworm-updates", "components": ["main", "contrib", "non-free"]}, {"url": "http://security.debian.org/debian-security", "distribution": "bookworm-security", "components": ["main", "contrib", "non-free"]}]}`.
      - <a id="properties/audit/properties/dpkg/properties/sources/additionalProperties"></a>**Additional properties** *(array)*
        - <a id="properties/audit/properties/dpkg/properties/sources/additionalProperties/items"></a>**Items** *(object)*
          - <a id="properties/audit/properties/dpkg/properties/sources/additionalProperties/items/properties/url"></a>**`url`** *(string)*: URL of the source.
          - <a id="properties/audit/properties/dpkg/properties/sources/additionalProperties/items/properties/distribution"></a>**`distribution`** *(string)*: Distribution of the source.
          - <a id="properties/audit/properties/dpkg/properties/sources/additionalProperties/items/properties/components"></a>**`components`** *(array)*: Components of the source.
            - <a id="properties/audit/properties/dpkg/properties/sources/additionalProperties/items/properties/components/items"></a>**Items** *(string)*
  - <a id="properties/audit/properties/version-mapping"></a>**`version-mapping`** *(object)*: Mapping of version to the branch name. Can contain additional properties. Default: `{}`.
    - <a id="properties/audit/properties/version-mapping/additionalProperties"></a>**Additional properties** *(string)*
  - <a id="properties/audit/properties/pre-commit"></a>**`pre-commit`** *(object)*: Cannot contain additional properties.
    - <a id="properties/audit/properties/pre-commit/properties/enabled"></a>**`enabled`** *(boolean)*: Enable pre-commit audit. Default: `true`.
    - <a id="properties/audit/properties/pre-commit/properties/skip-hooks"></a>**`skip-hooks`** *(array)*: List of pre-commit hooks to skip. Default: `[]`.
      - <a id="properties/audit/properties/pre-commit/properties/skip-hooks/items"></a>**Items** *(string)*
