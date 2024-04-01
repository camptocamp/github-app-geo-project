"""
Automatically generated file from a JSON schema.
"""

from typing import TypedDict

# Audit configuration.
AuditConfiguration = TypedDict(
    "AuditConfiguration",
    {
        # Dependency files that will not be installed
        "files-no-install": list[str],
        # Arguments to pass to pip install
        "pip-install-arguments": list[str],
        # Arguments to pass to pipenv sync
        "pipenv-sync-arguments": list[str],
        # Arguments to pass to snyk monitor
        "monitor-arguments": list[str],
        # Arguments to pass to snyk test
        "test-arguments": list[str],
        # Arguments to pass to snyk fix
        "fix-arguments": list[str],
    },
    total=False,
)


class AuditModulesConfiguration(TypedDict, total=False):
    """Audit modules configuration."""

    audit: "AuditConfiguration"
    """ Audit configuration. """
