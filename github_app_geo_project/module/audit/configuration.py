"""
Automatically generated file from a JSON schema.
"""

from typing import Any, TypedDict


class AuditConfiguration(TypedDict, total=False):
    """Audit configuration."""

    snyk: "SnykConfiguration"
    """ Snyk configuration. """

    dpkg: "DpkgConfiguration"
    """ Dpkg configuration. """


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
        {
            "url": "http://security.ubuntu.com/ubuntu",
            "distribution": "jammy-backports",
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
        {
            "url": "http://security.ubuntu.com/ubuntu",
            "distribution": "noble-backports",
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
    Enable Dpkg.

    Enable Dpkg audit

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
      - components:
        - main
        - restricted
        - universe
        - multiverse
        distribution: jammy-backports
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
      - components:
        - main
        - restricted
        - universe
        - multiverse
        distribution: noble-backports
        url: http://security.ubuntu.com/ubuntu
    """


ENABLE_DPKG_DEFAULT = True
""" Default value of the field path 'Dpkg configuration enabled' """


ENABLE_SNYK_DEFAULT = True
""" Default value of the field path 'Snyk configuration enabled' """


FILES_NOT_TO_INSTALL_DEFAULT: list[Any] = []
""" Default value of the field path 'Snyk configuration files-no-install' """


PIPENV_SYNC_ARGUMENTS_DEFAULT: list[Any] = []
""" Default value of the field path 'Snyk configuration pipenv-sync-arguments' """


PIP_INSTALL_ARGUMENTS_DEFAULT = ["--user"]
""" Default value of the field path 'Snyk configuration pip-install-arguments' """


SNYK_FIX_ARGUMENTS_DEFAULT = ["--all-projects"]
""" Default value of the field path 'Snyk configuration fix-arguments' """


SNYK_MONITOR_ARGUMENTS_DEFAULT = ["--all-projects"]
""" Default value of the field path 'Snyk configuration monitor-arguments' """


SNYK_TEST_ARGUMENTS_DEFAULT = ["--all-projects", "--severity-threshold=medium"]
""" Default value of the field path 'Snyk configuration test-arguments' """


# Snyk configuration.
SnykConfiguration = TypedDict(
    "SnykConfiguration",
    {
        # Enable Snyk.
        #
        # Enable Snyk audit
        #
        # default: True
        "enabled": bool,
        # Files not to install.
        #
        # Dependency files that will not be installed
        #
        # default:
        #   []
        "files-no-install": list[str],
        # Pip install arguments.
        #
        # Arguments to pass to pip install
        #
        # default:
        #   - --user
        "pip-install-arguments": list[str],
        # Pipenv sync arguments.
        #
        # Arguments to pass to pipenv sync
        #
        # default:
        #   []
        "pipenv-sync-arguments": list[str],
        # Snyk monitor arguments.
        #
        # Arguments to pass to snyk monitor
        #
        # default:
        #   - --all-projects
        "monitor-arguments": list[str],
        # Snyk test arguments.
        #
        # Arguments to pass to snyk test
        #
        # default:
        #   - --all-projects
        #   - --severity-threshold=medium
        "test-arguments": list[str],
        # Snyk fix arguments.
        #
        # Arguments to pass to snyk fix
        #
        # default:
        #   - --all-projects
        "fix-arguments": list[str],
    },
    total=False,
)


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
