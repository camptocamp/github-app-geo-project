"""
Automatically generated file from a JSON schema.
"""

from typing import Any, TypedDict

# Audit configuration.
AuditConfiguration = TypedDict(
    "AuditConfiguration",
    {
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
        #   - --fail-on=upgradable
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


class AuditModulesConfiguration(TypedDict, total=False):
    """Audit modules configuration."""

    audit: "AuditConfiguration"
    """ Audit configuration. """


FILES_NOT_TO_INSTALL_DEFAULT: list[Any] = []
""" Default value of the field path 'Audit configuration files-no-install' """


PIPENV_SYNC_ARGUMENTS_DEFAULT: list[Any] = []
""" Default value of the field path 'Audit configuration pipenv-sync-arguments' """


PIP_INSTALL_ARGUMENTS_DEFAULT = ["--user"]
""" Default value of the field path 'Audit configuration pip-install-arguments' """


SNYK_FIX_ARGUMENTS_DEFAULT = ["--all-projects"]
""" Default value of the field path 'Audit configuration fix-arguments' """


SNYK_MONITOR_ARGUMENTS_DEFAULT = ["--all-projects"]
""" Default value of the field path 'Audit configuration monitor-arguments' """


SNYK_TEST_ARGUMENTS_DEFAULT = ["--all-projects", "--fail-on=upgradable", "--severity-threshold=medium"]
""" Default value of the field path 'Audit configuration test-arguments' """
