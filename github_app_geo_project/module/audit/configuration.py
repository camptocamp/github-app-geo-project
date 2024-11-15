"""Automatically generated file from a JSON schema."""

from typing import Any, Literal, TypedDict, Union

# | Audit configuration.
AuditConfiguration = TypedDict(
    "AuditConfiguration",
    {
        # | Snyk configuration.
        "snyk": "SnykConfiguration",
        # | Dpkg configuration.
        "dpkg": "DpkgConfiguration",
        # | Version mapping.
        # |
        # | Mapping of version to the branch name
        # |
        # | default:
        # |   {}
        "version-mapping": dict[str, str],
    },
    total=False,
)


class AuditModulesConfiguration(TypedDict, total=False):
    """Audit modules configuration."""

    audit: "AuditConfiguration"
    """ Audit configuration. """


DPKG_SOURCES_DEFAULT = {
    "ubuntu_22_04": [
        {
            "url": "http://archive.ubuntu.com/ubuntu",
            "distribution": "jammy",
            "components": ["main", "restricted", "universe", "multiverse"],
        },
        {
            "url": "http://security.ubuntu.com/ubuntu",
            "distribution": "jammy-security",
            "components": ["main", "restricted", "universe", "multiverse"],
        },
        {
            "url": "http://security.ubuntu.com/ubuntu",
            "distribution": "jammy-updates",
            "components": ["main", "restricted", "universe", "multiverse"],
        },
    ],
    "ubuntu_24_04": [
        {
            "url": "http://archive.ubuntu.com/ubuntu",
            "distribution": "noble",
            "components": ["main", "restricted", "universe", "multiverse"],
        },
        {
            "url": "http://security.ubuntu.com/ubuntu",
            "distribution": "noble-security",
            "components": ["main", "restricted", "universe", "multiverse"],
        },
        {
            "url": "http://security.ubuntu.com/ubuntu",
            "distribution": "noble-updates",
            "components": ["main", "restricted", "universe", "multiverse"],
        },
    ],
    "debian_11": [
        {
            "url": "http://deb.debian.org/debian",
            "distribution": "bullseye",
            "components": ["main", "contrib", "non-free"],
        },
        {
            "url": "http://deb.debian.org/debian",
            "distribution": "bullseye-updates",
            "components": ["main", "contrib", "non-free"],
        },
        {
            "url": "http://security.debian.org/debian-security",
            "distribution": "bullseye-security",
            "components": ["main", "contrib", "non-free"],
        },
    ],
    "debian_12": [
        {
            "url": "http://deb.debian.org/debian",
            "distribution": "bookworm",
            "components": ["main", "contrib", "non-free"],
        },
        {
            "url": "http://deb.debian.org/debian",
            "distribution": "bookworm-updates",
            "components": ["main", "contrib", "non-free"],
        },
        {
            "url": "http://security.debian.org/debian-security",
            "distribution": "bookworm-security",
            "components": ["main", "contrib", "non-free"],
        },
    ],
}
""" Default value of the field path 'Dpkg configuration sources' """


class DpkgConfiguration(TypedDict, total=False):
    """Dpkg configuration."""

    enabled: bool
    """
    Enable dpkg.

    Enable dpkg audit

    default: True
    """

    sources: dict[str, list["_DpkgSourcesAdditionalpropertiesItem"]]
    """
    Dpkg sources.

    default:
      debian_11:
      - components:
        - main
        - contrib
        - non-free
        distribution: bullseye
        url: http://deb.debian.org/debian
      - components:
        - main
        - contrib
        - non-free
        distribution: bullseye-updates
        url: http://deb.debian.org/debian
      - components:
        - main
        - contrib
        - non-free
        distribution: bullseye-security
        url: http://security.debian.org/debian-security
      debian_12:
      - components:
        - main
        - contrib
        - non-free
        distribution: bookworm
        url: http://deb.debian.org/debian
      - components:
        - main
        - contrib
        - non-free
        distribution: bookworm-updates
        url: http://deb.debian.org/debian
      - components:
        - main
        - contrib
        - non-free
        distribution: bookworm-security
        url: http://security.debian.org/debian-security
      ubuntu_22_04:
      - components:
        - main
        - restricted
        - universe
        - multiverse
        distribution: jammy
        url: http://archive.ubuntu.com/ubuntu
      - components:
        - main
        - restricted
        - universe
        - multiverse
        distribution: jammy-security
        url: http://security.ubuntu.com/ubuntu
      - components:
        - main
        - restricted
        - universe
        - multiverse
        distribution: jammy-updates
        url: http://security.ubuntu.com/ubuntu
      ubuntu_24_04:
      - components:
        - main
        - restricted
        - universe
        - multiverse
        distribution: noble
        url: http://archive.ubuntu.com/ubuntu
      - components:
        - main
        - restricted
        - universe
        - multiverse
        distribution: noble-security
        url: http://security.ubuntu.com/ubuntu
      - components:
        - main
        - restricted
        - universe
        - multiverse
        distribution: noble-updates
        url: http://security.ubuntu.com/ubuntu
    """


ENABLE_DPKG_DEFAULT = True
""" Default value of the field path 'Dpkg configuration enabled' """


ENABLE_SNYK_DEFAULT = True
""" Default value of the field path 'Snyk configuration enabled' """


FILES_NOT_TO_INSTALL_DEFAULT: list[Any] = []
""" Default value of the field path 'Snyk configuration files-no-install' """


JAVA_PATH_BY_GRADLE_VERSION_DEFAULT: dict[str, Any] = {}
""" Default value of the field path 'Snyk configuration java-path-for-gradle' """


PIPENV_SYNC_ARGUMENTS_DEFAULT: list[Any] = []
""" Default value of the field path 'Snyk configuration pipenv-sync-arguments' """


PIP_INSTALL_ARGUMENTS_DEFAULT: list[Any] = []
""" Default value of the field path 'Snyk configuration pip-install-arguments' """


POETRY_INSTALL_ARGUMENTS_DEFAULT: list[Any] = []
""" Default value of the field path 'Snyk configuration poetry-install-arguments' """


SNYK_FIX_ARGUMENTS_DEFAULT = ["--all-projects"]
""" Default value of the field path 'Snyk configuration fix-arguments' """


SNYK_MONITOR_ARGUMENTS_DEFAULT = ["--all-projects"]
""" Default value of the field path 'Snyk configuration monitor-arguments' """


SNYK_TEST_ARGUMENTS_DEFAULT = ["--all-projects", "--severity-threshold=medium"]
""" Default value of the field path 'Snyk configuration test-arguments' """


# | Snyk configuration.
SnykConfiguration = TypedDict(
    "SnykConfiguration",
    {
        # | Enable Snyk.
        # |
        # | Enable Snyk audit
        # |
        # | default: True
        "enabled": bool,
        # | Files not to install.
        # |
        # | Dependency files that will not be installed
        # |
        # | default:
        # |   []
        "files-no-install": list[str],
        # | Pip install arguments.
        # |
        # | Arguments to pass to pip install
        # |
        # | default:
        # |   []
        "pip-install-arguments": list[str],
        # | Pipenv sync arguments.
        # |
        # | Arguments to pass to pipenv sync
        # |
        # | default:
        # |   []
        "pipenv-sync-arguments": list[str],
        # | Poetry install arguments.
        # |
        # | Arguments to pass to pip install
        # |
        # | default:
        # |   []
        "poetry-install-arguments": list[str],
        # | Java path by Gradle version.
        # |
        # | Path to the directory that contains Java executable to use for the Gradle minor version
        # |
        # | default:
        # |   {}
        "java-path-for-gradle": dict[str, str],
        # | Snyk monitor arguments.
        # |
        # | Arguments to pass to Snyk monitor
        # |
        # | default:
        # |   - --all-projects
        "monitor-arguments": list[str],
        # | Snyk test arguments.
        # |
        # | Arguments to pass to Snyk test
        # |
        # | default:
        # |   - --all-projects
        # |   - --severity-threshold=medium
        "test-arguments": list[str],
        # | Snyk fix arguments.
        # |
        # | Arguments to pass to Snyk fix
        # |
        # | default:
        # |   - --all-projects
        "fix-arguments": list[str],
        # | Snyk monitor configuration.
        "monitor": "SnykMonitorConfiguration",
    },
    total=False,
)


# | Snyk monitor configuration.
SnykMonitorConfiguration = TypedDict(
    "SnykMonitorConfiguration",
    {
        # | Snyk monitor environment.
        # |
        # | Set the project environment project attribute. To clear the project environment set empty array.
        # | For more information see Project attributes https://docs.snyk.io/getting-started/introduction-to-snyk-projects/view-project-information/project-attributes
        "project-environment": list["_SnykMonitorEnvironmentItem"],
        # | Snyk monitor lifecycle.
        # |
        # | Set the project lifecycle project attribute. To clear the project lifecycle set empty array.
        # | For more information see Project attributes https://docs.snyk.io/snyk-admin/snyk-projects/project-tags
        "project-lifecycle": list["_SnykMonitorLifecycleItem"],
        # | Snyk monitor business criticality.
        # |
        # | Set the project business criticality project attribute. To clear the project business criticality set empty array.
        # | For more information see Project attributes https://docs.snyk.io/snyk-admin/snyk-projects/project-tags
        "project-business-criticality": list["_SnykMonitorBusinessCriticalityItem"],
        # | Snyk monitor tags.
        # |
        # | Set the project tags to one or more values.
        # | To clear the project tags set empty dictionary.
        "project-tags": dict[str, str],
    },
    total=False,
)


VERSION_MAPPING_DEFAULT: dict[str, Any] = {}
""" Default value of the field path 'Audit configuration version-mapping' """


class _DpkgSourcesAdditionalpropertiesItem(TypedDict, total=False):
    url: str
    """
    URL.

    URL of the source
    """

    distribution: str
    """
    Distribution.

    Distribution of the source
    """

    components: list[str]
    """
    Components.

    Components of the source
    """


_SnykMonitorBusinessCriticalityItem = Union[
    Literal["critical"], Literal["high"], Literal["medium"], Literal["low"]
]
_SNYKMONITORBUSINESSCRITICALITYITEM_CRITICAL: Literal["critical"] = "critical"
"""The values for the '_SnykMonitorBusinessCriticalityItem' enum"""
_SNYKMONITORBUSINESSCRITICALITYITEM_HIGH: Literal["high"] = "high"
"""The values for the '_SnykMonitorBusinessCriticalityItem' enum"""
_SNYKMONITORBUSINESSCRITICALITYITEM_MEDIUM: Literal["medium"] = "medium"
"""The values for the '_SnykMonitorBusinessCriticalityItem' enum"""
_SNYKMONITORBUSINESSCRITICALITYITEM_LOW: Literal["low"] = "low"
"""The values for the '_SnykMonitorBusinessCriticalityItem' enum"""


_SnykMonitorEnvironmentItem = Union[
    Literal["frontend"],
    Literal["backend"],
    Literal["internal"],
    Literal["external"],
    Literal["mobile"],
    Literal["saas"],
    Literal["onprem"],
    Literal["hosted"],
    Literal["distributed"],
]
_SNYKMONITORENVIRONMENTITEM_FRONTEND: Literal["frontend"] = "frontend"
"""The values for the '_SnykMonitorEnvironmentItem' enum"""
_SNYKMONITORENVIRONMENTITEM_BACKEND: Literal["backend"] = "backend"
"""The values for the '_SnykMonitorEnvironmentItem' enum"""
_SNYKMONITORENVIRONMENTITEM_INTERNAL: Literal["internal"] = "internal"
"""The values for the '_SnykMonitorEnvironmentItem' enum"""
_SNYKMONITORENVIRONMENTITEM_EXTERNAL: Literal["external"] = "external"
"""The values for the '_SnykMonitorEnvironmentItem' enum"""
_SNYKMONITORENVIRONMENTITEM_MOBILE: Literal["mobile"] = "mobile"
"""The values for the '_SnykMonitorEnvironmentItem' enum"""
_SNYKMONITORENVIRONMENTITEM_SAAS: Literal["saas"] = "saas"
"""The values for the '_SnykMonitorEnvironmentItem' enum"""
_SNYKMONITORENVIRONMENTITEM_ONPREM: Literal["onprem"] = "onprem"
"""The values for the '_SnykMonitorEnvironmentItem' enum"""
_SNYKMONITORENVIRONMENTITEM_HOSTED: Literal["hosted"] = "hosted"
"""The values for the '_SnykMonitorEnvironmentItem' enum"""
_SNYKMONITORENVIRONMENTITEM_DISTRIBUTED: Literal["distributed"] = "distributed"
"""The values for the '_SnykMonitorEnvironmentItem' enum"""


_SnykMonitorLifecycleItem = Union[Literal["production"], Literal["development"], Literal["sandbox"]]
_SNYKMONITORLIFECYCLEITEM_PRODUCTION: Literal["production"] = "production"
"""The values for the '_SnykMonitorLifecycleItem' enum"""
_SNYKMONITORLIFECYCLEITEM_DEVELOPMENT: Literal["development"] = "development"
"""The values for the '_SnykMonitorLifecycleItem' enum"""
_SNYKMONITORLIFECYCLEITEM_SANDBOX: Literal["sandbox"] = "sandbox"
"""The values for the '_SnykMonitorLifecycleItem' enum"""
