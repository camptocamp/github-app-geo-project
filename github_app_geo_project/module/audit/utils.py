# Copyright (c) 2026, Camptocamp SA

"""The auditing functions."""

import asyncio
import base64
import datetime as dt
import html
import io
import json
import logging
import os
import re
import subprocess
import tempfile
import tomllib
import urllib.parse
from pathlib import Path
from textwrap import dedent
from typing import Any, NamedTuple, cast

import aiohttp
import anyio
import apt_repo
import debian_inspector.version
import packaging.requirements
import packaging.specifiers
import packaging.version
import security_md
import tag_publish.configuration
import yaml  # nosec
from pydantic import BaseModel

from github_app_geo_project import models, utils
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.module.audit import configuration
from github_app_geo_project.settings import settings

_LOGGER = logging.getLogger(__name__)

# Add timeout environment variables with defaults at module level
_TIMEOUT_SUBPROCESS = settings.audit.timeouts.subprocess
_TIMEOUT_PIP_FREEZE = settings.audit.timeouts.pip_freeze
_TIMEOUT_PREK = settings.audit.timeouts.prek
_TIMEOUT_GIT_DIFF = settings.audit.timeouts.git_diff
_TIMEOUT_GRADLE = settings.audit.timeouts.gradle
_TIMEOUT_GRADLE_LOCKS = settings.audit.timeouts.gradle_locks
_TIMEOUT_GIT_LSFILES = settings.audit.timeouts.git_lsfiles
_TIMEOUT_PYTHON_INSTALL = settings.audit.timeouts.python_install
_TIMEOUT_SNYK = settings.audit.timeouts.snyk
_TIMEOUT_SNYK_FIX = settings.audit.timeouts.snyk_fix
_TIMEOUT_POETRY_VERSION = settings.audit.timeouts.poetry_version
_TIMEOUT_NPM_AUDIT = settings.audit.timeouts.npm_audit
_TIMEOUT_NODE_INSTALL = settings.audit.timeouts.node_install
_TIMEOUT_HELM_TEMPLATE = settings.audit.timeouts.helm_template
_TIMEOUT_TRIVY_IMAGE = settings.audit.timeouts.trivy_image
_TIMEOUT_TRIVY_FS = settings.audit.timeouts.trivy_fs
_TIMEOUT_REGISTRY = settings.audit.timeouts.registry
_TIMEOUT_POETRY_LOCK = settings.audit.timeouts.poetry_lock
_TIMEOUT_POETRY_CHECK_LOCK = settings.audit.timeouts.poetry_check_lock
_TIMEOUT_PIP_DRY_RUN = settings.audit.timeouts.pip_dry_run
_TIMEOUT_COSIGN = settings.audit.timeouts.cosign_attestation

_TRIVY_CONCURRENCY = 4
"""Number of container images scanned by Trivy at the same time."""

_TRIVY_CACHE_DIR = "/var/www/.cache/trivy"
"""The Trivy cache (vulnerability DB) directory, writable by the worker user."""

_COSIGN_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
"""The OIDC issuer of the keyless cosign signatures created by the GitHub Actions publishing jobs."""

_GHCR_HOST = "ghcr.io"
"""The only registry host we can map back to a GitHub organization for the attestation policy."""


class VulnerabilityData(NamedTuple):
    """Structured data for a single vulnerability from Snyk."""

    file: str
    """The target file path (displayTargetFile)"""
    package_name: str
    """The vulnerable package name"""
    package_version: str
    """The vulnerable package version"""
    package_manager: str
    """The package manager (pip, npm, etc.)"""
    severity: str
    """The severity level (low, medium, high, critical)"""
    snyk_id: str
    """The Snyk vulnerability ID"""
    cve_ids: list[str]
    """List of CVE identifiers"""
    cwe_ids: list[str]
    """List of CWE identifiers"""
    title: str
    """The formatted title for dashboard display"""
    fixed_in: list[str]
    """List of versions that fix this vulnerability"""
    is_upgradable: bool
    """Whether the vulnerability is upgradable"""
    is_patchable: bool
    """Whether the vulnerability is patchable"""
    id_url: str = ""
    """The URL of the vulnerability page for the main ID, empty for Snyk (the Snyk URL is built from the ID)"""


class PinnedVulnerability(NamedTuple):
    """A vulnerability addressed by a version pin added to a requirements file by the Snyk fix."""

    vulnerability: VulnerabilityData
    """The vulnerability data from the scan done before the fix."""
    pinned_version: str
    """The version pinned in the requirements file."""


class SnykAuditResult(NamedTuple):
    """The result of a Snyk audit run on a branch."""

    messages: list[module_utils.Message]
    """The output messages (install errors, high or upgradable vulnerabilities)."""
    fix_message: module_utils.HtmlMessage | None
    """The message of the fix commit."""
    short_messages: list[str]
    """The dashboard's messages (with a resume of the vulnerabilities)."""
    success: bool
    """Is on success (errors: vulnerability that can be fixed by upgrading the dependency)."""
    vulnerabilities: dict[str, list[VulnerabilityData]]
    """The file-grouped vulnerability data for dashboard display and advisory creation."""
    fixed_vulnerabilities: dict[str, list[VulnerabilityData]]
    """The file-grouped vulnerabilities fixed by this run, confirmed by the rescan."""
    pinned_vulnerabilities: dict[str, list[PinnedVulnerability]]
    """The file-grouped vulnerabilities addressed by pins added to the requirements files."""


# Map Snyk package managers to GitHub advisory ecosystems
ECOSYSTEM_MAP: dict[str, str] = {
    "pip": "pip",
    "poetry": "pip",
    "npm": "npm",
    "maven": "maven",
    "nuget": "nuget",
    "composer": "composer",
    "gomodules": "go",
    "rubygems": "rubygems",
    "cargo": "rust",
    "cocoapods": "other",
    "hex": "other",
    "linux": "other",
    "deb": "other",
    "docker": "other",
    "apk": "other",
}


SEVERITY_ORDER: dict[str, int] = {
    "low": 0,
    "medium": 1,
    "high": 2,
    "critical": 3,
}


def get_severity_config(
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    key: str,
    default: str,
) -> str:
    """Get a severity threshold configuration value."""
    return local_config.get(key, config.get(key, default))  # type: ignore[return-value]


def get_excluded_files(
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
) -> list[str]:
    """Get the list of excluded file regex patterns."""
    return local_config.get("excluded-files", config.get("excluded-files", []))


def get_pre_commit_config(
    config: configuration.AuditConfiguration,
    local_config: configuration.AuditConfiguration,
) -> configuration.PreCommitConfiguration:
    """Get the pre-commit configuration."""
    pre_commit_config = config.get("pre-commit", {})
    local_pre_commit_config = local_config.get("pre-commit", {})
    return {
        "enabled": local_pre_commit_config.get(
            "enabled",
            pre_commit_config.get("enabled", configuration.ENABLE_PRE_COMMIT_DEFAULT),
        ),
        "skip-hooks": local_pre_commit_config.get(
            "skip-hooks",
            pre_commit_config.get("skip-hooks", configuration.SKIP_HOOKS_DEFAULT),
        ),
    }


def _vulnerability_key(vulnerability: VulnerabilityData) -> tuple[str, str, str, str]:
    """Get the identity key of a vulnerability, used to compare the scans done before and after a fix."""
    return (
        vulnerability.file,
        vulnerability.package_name,
        vulnerability.package_version,
        vulnerability.snyk_id,
    )


def _vulnerability_sort_key(vulnerability: VulnerabilityData) -> tuple[int, str, str]:
    """Sort the vulnerabilities by descending severity, then by package name and version."""
    return (
        -SEVERITY_ORDER.get(vulnerability.severity, 0),
        vulnerability.package_name,
        vulnerability.package_version,
    )


def fixed_vulnerabilities(
    before: dict[str, list[VulnerabilityData]],
    after: dict[str, list[VulnerabilityData]],
) -> dict[str, list[VulnerabilityData]]:
    """
    Get the file-grouped vulnerabilities that were fixed by the Snyk/npm fix.

    They are the ones present in the scan done before the fix and not anymore in the scan done after it.
    """
    after_keys = {
        _vulnerability_key(vulnerability)
        for vulnerabilities in after.values()
        for vulnerability in vulnerabilities
    }
    fixed: dict[str, list[VulnerabilityData]] = {}
    for file_name, vulnerabilities in before.items():
        for vulnerability in vulnerabilities:
            if _vulnerability_key(vulnerability) in after_keys:
                continue
            fixed.setdefault(file_name, []).append(vulnerability)
    for vulnerabilities in fixed.values():
        vulnerabilities.sort(key=_vulnerability_sort_key)
    return dict(sorted(fixed.items()))


_PIN_LINE_RE = re.compile(
    r"^\+\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\s*\[[^\]]*\])?"
    r"\s*(?:==|>=|~=)\s*"
    r"(?P<version>[0-9][0-9A-Za-z._+-]*)"
)
"""Matches an added requirements line with a version pin, in a `git diff --unified=0` output."""


def _normalize_package_name(name: str) -> str:
    """Normalize a Python package name as defined by PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _normalize_file_name(name: str) -> str:
    """Normalize a file path so the git diff and the Snyk target files can be compared."""
    return name.removeprefix("./")


def parse_requirements_pins(diff: str) -> dict[str, set[tuple[str, str]]]:
    """
    Extract the version pins added to the requirements files.

    The input is the output of `git diff --unified=0` taken after the Snyk/npm fix, the
    pins are grouped by normalized file path, with normalized package names (PEP 503).
    """
    pins: dict[str, set[tuple[str, str]]] = {}
    current_file: str | None = None
    for line in diff.splitlines():
        if line.startswith("+++ "):
            path = line[len("+++ ") :].strip()
            if path == "/dev/null" or not path.endswith(".txt"):
                current_file = None
            else:
                current_file = _normalize_file_name(path.removeprefix("b/"))
            continue
        if current_file is None or not line.startswith("+"):
            continue
        match = _PIN_LINE_RE.match(line)
        if match is not None:
            pins.setdefault(current_file, set()).add(
                (_normalize_package_name(match["name"]), match["version"]),
            )
    return pins


def pin_fixed_vulnerabilities(
    before: dict[str, list[VulnerabilityData]],
    pins: dict[str, set[tuple[str, str]]],
    already_fixed: dict[str, list[VulnerabilityData]],
) -> dict[str, list[PinnedVulnerability]]:
    """
    Get the file-grouped vulnerabilities addressed by pins added to the requirements files.

    Snyk builds the pip dependency tree from the packages installed in the audit environment,
    which is not reinstalled after the fix, so the rescan still reports the vulnerabilities
    fixed by a pin added to a requirements file. They are matched with the scan done before
    the fix by file and package name, and only if the pinned version fixes the vulnerability.
    The ones already confirmed fixed by the rescan are excluded, to not report them twice.
    """
    fixed_keys = {
        _vulnerability_key(vulnerability)
        for vulnerabilities in already_fixed.values()
        for vulnerability in vulnerabilities
    }
    pinned: dict[str, list[PinnedVulnerability]] = {}
    for file_name, vulnerabilities in before.items():
        file_pins = pins.get(_normalize_file_name(file_name))
        if not file_pins:
            continue
        for vulnerability in vulnerabilities:
            if _vulnerability_key(vulnerability) in fixed_keys:
                continue
            for package_name, version in sorted(file_pins):
                if (
                    package_name == _normalize_package_name(vulnerability.package_name)
                    and version in vulnerability.fixed_in
                ):
                    pinned.setdefault(file_name, []).append(
                        PinnedVulnerability(vulnerability=vulnerability, pinned_version=version),
                    )
                    break
    for pinned_vulnerabilities in pinned.values():
        pinned_vulnerabilities.sort(key=lambda item: _vulnerability_sort_key(item.vulnerability))
    return dict(sorted(pinned.items()))


_PYPI_JSON_URL = "https://pypi.org/pypi/{name}/{version}/json"
"""PyPI JSON metadata URL template, used to read the ``Requires-Python`` of a release."""

_REQUIREMENT_LINE_RE = re.compile(
    r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?:\s*\[[^\]]*\])?"
    r"\s*(?:==|>=|~=|<=|>|<)\s*"
    r"(?P<version>[0-9][0-9A-Za-z._+-]*)"
)
"""Matches a requirement line with a version constraint inside a requirements file."""


def is_python_incompatible(requires_python: str | None, python_version: str) -> bool:
    """
    Tell whether a release cannot run on the given Python version.

    ``requires_python`` is the release ``Requires-Python`` specifier read from PyPI and
    ``python_version`` the branch Python ``major.minor``. A missing specifier or an
    unparsable value is treated as compatible, so a pin is never dropped on a doubt.
    """
    if not requires_python or not python_version:
        return False
    try:
        version = packaging.version.Version(python_version)
        specifiers = packaging.specifiers.SpecifierSet(requires_python)
    except (packaging.specifiers.InvalidSpecifier, packaging.version.InvalidVersion):
        return False
    return not specifiers.contains(version, prereleases=True)


def remove_pin_lines(content: str, pins: set[tuple[str, str]]) -> tuple[str, bool]:
    """
    Remove the requirement lines pinning one of the given packages.

    ``pins`` items are ``(package name, version)``; package names are normalized (PEP 503)
    on both sides. Returns the new content and whether at least one line was removed.
    """
    if not pins:
        return content, False
    targets = {(_normalize_package_name(name), version) for name, version in pins}
    changed = False
    kept: list[str] = []
    for line in content.splitlines(keepends=True):
        match = _REQUIREMENT_LINE_RE.match(line)
        if match is not None and (_normalize_package_name(match["name"]), match["version"]) in targets:
            changed = True
            continue
        kept.append(line)
    return "".join(kept), changed


async def _fetch_requires_python(
    session: aiohttp.ClientSession,
    package_name: str,
    version: str,
) -> str | None:
    """Read the ``Requires-Python`` of a PyPI release, or None when it cannot be determined."""
    url = _PYPI_JSON_URL.format(
        name=urllib.parse.quote(package_name, safe=""),
        version=urllib.parse.quote(version, safe=""),
    )
    try:
        async with session.get(url) as response:
            if not response.ok:
                _LOGGER.debug("PyPI request %s returned status %s", url, response.status)
                return None
            data: dict[str, Any] = json.loads(await response.read())
    except (aiohttp.ClientError, TimeoutError) as error:
        _LOGGER.warning("Error while requesting the PyPI metadata %s: %s", url, error)
        return None
    info = data.get("info")
    if not isinstance(info, dict):
        return None
    requires_python = info.get("requires_python")
    return requires_python if isinstance(requires_python, str) else None


async def _drop_pins_in_file(
    cwd: anyio.Path,
    file_name: str,
    pins: set[tuple[str, str]],
) -> list[str]:
    """Remove the given pins from a requirements file, return the dropped ``name>=version``."""
    path = cwd / file_name
    if not await path.exists():
        return []
    content = await path.read_text(encoding="utf-8")
    new_content, changed = remove_pin_lines(content, pins)
    if not changed:
        return []
    await path.write_text(new_content, encoding="utf-8")
    return [f"{name}>={version}" for name, version in sorted(pins)]


async def drop_python_incompatible_pins(
    cwd: anyio.Path,
    pins_by_file: dict[str, set[tuple[str, str]]],
    python_version: str,
) -> list[str]:
    """
    Drop the requirement pins that cannot run on the branch Python version.

    Snyk appends a minimum-version lower bound to the requirements files for every
    vulnerability it reports, without checking that the fixed release is installable with
    the branch interpreter. When it requires a newer Python than the branch, the Docker
    image ``pip install`` fails deterministically; such a pin is removed here so it is
    neither committed nor reported as a fix. Returns the dropped ``name>=version`` list.
    """
    if not python_version or not pins_by_file:
        return []

    unique_pins = {(name, version) for pins in pins_by_file.values() for name, version in pins}
    requires_python: dict[tuple[str, str], str | None] = {}
    timeout = aiohttp.ClientTimeout(total=settings.audit.timeouts.snyk_api.total_seconds())
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with asyncio.TaskGroup() as group:
            fetch_tasks = {
                key: group.create_task(_fetch_requires_python(session, key[0], key[1])) for key in unique_pins
            }
        for key, fetch_task in fetch_tasks.items():
            requires_python[key] = fetch_task.result()

    incompatible = {
        key for key, spec in requires_python.items() if is_python_incompatible(spec, python_version)
    }
    if not incompatible:
        return []

    dropped: list[str] = []
    async with asyncio.TaskGroup() as group:
        file_tasks = [
            group.create_task(
                _drop_pins_in_file(
                    cwd,
                    file_name,
                    {(name, version) for name, version in pins if (name, version) in incompatible},
                ),
            )
            for file_name, pins in pins_by_file.items()
            if any((name, version) in incompatible for name, version in pins)
        ]
    for file_task in file_tasks:
        dropped.extend(file_task.result())
    return dropped


async def snyk(
    branch: str,
    owner: str,
    repository: str,
    audit_config: configuration.AuditConfiguration,
    audit_local_config: configuration.AuditConfiguration,
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    logs_url: str,
    env: dict[str, str],
    cwd: anyio.Path,
    python_version: str = "",
    ignore_policy: bool = False,
) -> SnykAuditResult:
    """
    Audit the code with Snyk.

    ``python_version`` is the branch Python ``major.minor``: requirement pins whose fixed
    release requires a newer Python are dropped, as they break the image ``pip install``.

    Return the audit result, see :class:`SnykAuditResult`.
    """
    result: list[module_utils.Message] = []

    env["PATH"] = f"{env['HOME']}/.local/bin:{env['PATH']}"

    await _select_java_version(config, local_config, env, cwd)

    await _select_node_version(env, cwd)

    _LOGGER.debug("Updated path: %s", env["PATH"])

    await _install_requirements_dependencies(config, local_config, result, env, cwd)

    command = ["pip", "freeze"]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        cwd=cwd,
    )  # nosec
    async with asyncio.timeout(_TIMEOUT_PIP_FREEZE.total_seconds()):
        stdout, stderr = await proc.communicate()
    message = module_utils.AnsiProcessMessage.from_async_artifacts(command, proc, stdout, stderr)
    message.title = "Pip freeze"
    _LOGGER.info(message)

    await _install_pipenv_dependencies(config, local_config, result, env, cwd)
    poetry_install_dirs = await _install_poetry_dependencies(config, local_config, result, env, cwd)

    try:
        env["FORCE_COLOR"] = "true"
        env_no_debug = {**env}
        env["DEBUG"] = "*snyk*"  # debug mode

        monitor_started_at = dt.datetime.now(dt.UTC)
        monitor_success = await _snyk_monitor(branch, config, local_config, result, env, cwd)
        if monitor_success:
            # Remove the projects that were not refreshed by this monitor run (dependency files
            # that are not scanned anymore), with a tolerance for the clock desynchronization.
            await snyk_cleanup_stale_projects(
                owner,
                repository,
                branch,
                monitor_started_at - _SNYK_MONITOR_SKEW,
                result,
            )

        (
            high_vulnerabilities,
            fixable_vulnerabilities,
            fixable_vulnerabilities_summary,
            fixable_files_npm,
            vulnerabilities_in_requirements,
            vulnerabilities_before_fix,
        ) = await _snyk_test(
            branch, config, local_config, result, env_no_debug, cwd, ignore_policy=ignore_policy
        )

        auto_fix = local_config.get(
            "auto-fix",
            config.get("auto-fix", configuration.AUTOMATIC_FIX_DEFAULT),
        )
        if auto_fix:
            snyk_fix_success, snyk_fix_message = await _snyk_fix(
                branch,
                cwd,
                config,
                local_config,
                logs_url,
                result,
                env_no_debug,
                env,
                fixable_vulnerabilities_summary,
                vulnerabilities_in_requirements,
            )
            npm_audit_fix_message, npm_audit_fix_success = await _npm_audit_fix(
                fixable_files_npm, result, cwd, env_no_debug
            )
        else:
            # Report the vulnerabilities without changing any file: the fix commands are skipped,
            # the git diff stays empty and no audit pull request is created.
            snyk_fix_success, snyk_fix_message = True, None
            npm_audit_fix_message, npm_audit_fix_success = None, True
            disabled_message = module_utils.HtmlMessage(
                "The automatic fixes are disabled by the `auto-fix` configuration, "
                "the fixable vulnerabilities are only reported.",
            )
            disabled_message.title = "Auto-fix disabled"
            result.append(disabled_message)
        # Get the changes applied by the fix commands, before `prek` eventually reformats
        # other files, to report the pins added to the requirements files.
        fix_diff, _, _ = await module_utils.run_timeout(
            ["git", "diff", "--unified=0"],
            None,
            _TIMEOUT_GIT_DIFF,
            "Git diff of the applied fixes",
            "Error while getting the git diff",
            "Timeout while getting the git diff",
            cwd,
        )
        # Drop the requirement pins whose fixed release needs a newer Python than the branch:
        # they make the Docker image `pip install` fail, and Snyk adds them without checking.
        if python_version:
            dropped_pins = await drop_python_incompatible_pins(
                cwd,
                parse_requirements_pins(fix_diff or ""),
                python_version,
            )
            if dropped_pins:
                _LOGGER.info(
                    "Skipped the requirement pins incompatible with Python %s: %s",
                    python_version,
                    ", ".join(dropped_pins),
                )
                skipped_message = module_utils.HtmlMessage(
                    f"Skipped pins incompatible with Python {python_version}: {', '.join(dropped_pins)}",
                )
                skipped_message.title = "Python-incompatible pins skipped"
                result.append(skipped_message)
                fix_diff, _, _ = await module_utils.run_timeout(
                    ["git", "diff", "--unified=0"],
                    None,
                    _TIMEOUT_GIT_DIFF,
                    "Git diff after dropping the incompatible pins",
                    "Error while getting the git diff",
                    "Timeout while getting the git diff",
                    cwd,
                )
        fix_message: module_utils.HtmlMessage | None = None
        if snyk_fix_message is None:
            if npm_audit_fix_message:
                fix_message = module_utils.HtmlMessage(npm_audit_fix_message)
                fix_message.title = "Npm audit fix"
        else:
            fix_message = snyk_fix_message
            if npm_audit_fix_message:
                assert isinstance(fix_message, module_utils.HtmlMessage)
                fix_message.html = f"{fix_message.html}<br>\n<br>\n{npm_audit_fix_message}"
        fix_has_errors = len(fixable_vulnerabilities_summary) > 0 and not (
            snyk_fix_success and npm_audit_fix_success
        )
        fix_success = True

        pre_commit_config = get_pre_commit_config(audit_config, audit_local_config)
        if pre_commit_config.get("enabled", True) and await (cwd / ".pre-commit-config.yaml").exists():
            command = [
                "prek",
                "run",
                "--all-files",
                "--show-diff-on-failure",
                "--config=.pre-commit-config.yaml",
            ]
            proc = await asyncio.create_subprocess_exec(
                *command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                cwd=cwd,
                env={
                    **os.environ,
                    "SKIP": ",".join(
                        pre_commit_config.get("skip-hooks", []),
                    ),
                },
            )
            async with asyncio.timeout(_TIMEOUT_PREK.total_seconds()):
                stdout, stderr = await proc.communicate()
            message = module_utils.AnsiProcessMessage.from_async_artifacts(command, proc, stdout, stderr)
            message.title = "Run prek"
            _LOGGER.debug(message)

        command = ["git", "diff", "--quiet"]
        diff_proc = await asyncio.create_subprocess_exec(*command, cwd=cwd)
        async with asyncio.timeout(_TIMEOUT_GIT_DIFF.total_seconds()):
            await diff_proc.wait()
        if diff_proc.returncode != 0:
            (
                high_vulnerabilities,
                fixable_vulnerabilities,
                fixable_vulnerabilities_summary,
                fixable_files_npm,
                vulnerabilities_in_requirements,
                file_vulnerabilities,
            ) = await _snyk_test(
                branch, config, local_config, result, env_no_debug, cwd, ignore_policy=ignore_policy
            )
            fixed = fixed_vulnerabilities(vulnerabilities_before_fix, file_vulnerabilities)
            pinned = pin_fixed_vulnerabilities(
                vulnerabilities_before_fix,
                parse_requirements_pins(fix_diff or ""),
                fixed,
            )
        else:
            # The fix did not change anything, the vulnerabilities are the ones of the first scan.
            file_vulnerabilities = vulnerabilities_before_fix
            fixed = {}
            pinned = {}

        return_message = [
            *[f"{number} {severity} vulnerabilities" for severity, number in high_vulnerabilities.items()],
            *[
                f"{number} {severity} vulnerabilities can be fixed"
                for severity, number in fixable_vulnerabilities.items()
            ],
            *([] if not fix_has_errors else ["Error while fixing the vulnerabilities"]),
        ]

        return SnykAuditResult(
            messages=result,
            fix_message=fix_message,
            short_messages=return_message,
            success=fix_success,
            vulnerabilities=file_vulnerabilities,
            fixed_vulnerabilities=fixed,
            pinned_vulnerabilities=pinned,
        )
    finally:
        await _cleanup_poetry_envs(poetry_install_dirs, env)


_GRADLE_MAX_JAVA_VERSION = {6: 11, 7: 17, 8: 21}
"""Maximum Java major version able to run each Gradle major version."""


async def find_compatible_java_path(
    gradle_version: str,
    jvm_root: anyio.Path | None = None,
) -> str | None:
    """
    Find the bin folder of the newest installed OpenJDK able to run the Gradle version.

    Returns None when the system default Java should be used (recent or unknown Gradle
    versions) or when no compatible Java installation is found.
    """
    try:
        gradle_major = int(gradle_version.split(".", maxsplit=1)[0])
    except ValueError:
        _LOGGER.warning("Unable to parse the Gradle version %s", gradle_version)
        return None
    max_java = _GRADLE_MAX_JAVA_VERSION.get(gradle_major)
    if max_java is None:
        # The recent (or unknown) Gradle versions run with the system default Java
        return None
    if jvm_root is None:
        jvm_root = anyio.Path("/usr/lib/jvm")
    if not await jvm_root.exists():
        _LOGGER.warning("The JVM root %s does not exist", jvm_root)
        return None
    best_version = 0
    best_path: str | None = None
    async for entry in jvm_root.iterdir():
        match = re.match(r"^java-(\d+)-openjdk", entry.name)
        if match is None:
            continue
        java_version = int(match.group(1))
        if java_version > max_java or java_version <= best_version:
            continue
        if await (entry / "bin" / "java").exists():
            best_version = java_version
            best_path = str(entry / "bin")
    if best_path is None:
        _LOGGER.warning(
            "No installed OpenJDK <= %s found in %s to run Gradle %s",
            max_java,
            jvm_root,
            gradle_version,
        )
        return None
    _LOGGER.info("Using Java %s to run Gradle %s: %s", best_version, gradle_version, best_path)
    return best_path


async def _select_java_version(
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    env: dict[str, str],
    cwd: anyio.Path,
) -> None:
    if not await (cwd / "gradlew").exists():
        return

    command = ["./gradlew", "--version"]
    proc = await asyncio.create_subprocess_exec(  # nosec
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    async with asyncio.timeout(_TIMEOUT_GRADLE.total_seconds()):
        stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise subprocess.CalledProcessError(
            proc.returncode if proc.returncode is not None else -999,
            command,
            stdout,
            stderr,
        )
    gradle_version_out = stdout.decode().splitlines()
    gradle_version_out_filter = [line for line in gradle_version_out if line.startswith("Gradle ")]
    gradle_version = gradle_version_out_filter[0].split()[1]

    minor_gradle_version = ".".join(gradle_version.split(".")[0:2])

    java_path_for_gradle = local_config.get("java-path-for-gradle", config.get("java-path-for-gradle", {}))
    if minor_gradle_version not in java_path_for_gradle:
        _LOGGER.warning(
            "Gradle version %s is not in the configuration: %s, "
            "trying to select a compatible installed Java version.",
            minor_gradle_version,
            ", ".join(java_path_for_gradle.keys()),
        )
        java_path = await find_compatible_java_path(gradle_version)
        if java_path is not None:
            env["PATH"] = f"{java_path}:{env['PATH']}"
            return
        _LOGGER.debug("Gradle version out: %s", "\n".join(gradle_version_out))
        await module_utils.run_timeout(
            ["./gradlew", "--version"],
            env,
            _TIMEOUT_SUBPROCESS,
            "Gradle version",
            "Error on getting Gradle version",
            "Timeout on getting Gradle version",
            cwd,
        )
        return

    env["PATH"] = f"{java_path_for_gradle[minor_gradle_version]}:{env['PATH']}"


_NODE_INSTALL_LOCKS: dict[str, asyncio.Lock] = {}


def get_fnm_root() -> anyio.Path:
    """Get the fnm root directory, same resolution as fnm itself on Linux."""
    # pathlib.Path is OK here: pure path manipulation, no I/O (like get_pyenv_root).
    return anyio.Path(os.environ.get("FNM_DIR") or Path.home() / ".local" / "share" / "fnm")


async def find_node_version_spec(cwd: anyio.Path) -> str | None:
    """Get the Node.js version specification pinned by the repository, from its version files."""
    for file_name in (".nvmrc", ".node-version"):
        version_file = cwd / file_name
        if await version_file.exists():
            for line in (await version_file.read_text(encoding="utf-8")).splitlines():
                if line.strip():
                    return line.strip()
    tool_versions = cwd / ".tool-versions"
    if await tool_versions.exists():
        for line in (await tool_versions.read_text(encoding="utf-8")).splitlines():
            if line.startswith("nodejs "):
                return line.split(" ", maxsplit=1)[1].strip()
    return None


async def ensure_fnm_node(node_version_spec: str) -> bool:
    """Install a Node.js version with fnm if not already installed, and return the success."""
    # The lock creation is safe: asyncio is single-threaded and setdefault doesn't await.
    lock = _NODE_INSTALL_LOCKS.setdefault(node_version_spec, asyncio.Lock())
    async with lock:
        fnm_root = get_fnm_root()
        await fnm_root.mkdir(parents=True, exist_ok=True)
        _, success, _ = await module_utils.run_timeout(
            ["fnm", "install", node_version_spec],
            None,
            _TIMEOUT_NODE_INSTALL,
            f"Install the Node.js version {node_version_spec} with fnm",
            f"Error while installing the Node.js version {node_version_spec} with fnm",
            f"Timeout while installing the Node.js version {node_version_spec} with fnm",
            fnm_root,
        )
    return success


async def _select_node_version(
    env: dict[str, str],
    cwd: anyio.Path,
) -> None:
    """Add the Node.js version pinned by the repository to the PATH, lazily installed with fnm."""
    node_version_spec = await find_node_version_spec(cwd)
    if node_version_spec is None:
        return
    if not await ensure_fnm_node(node_version_spec):
        _LOGGER.warning(
            "Unable to install the Node.js version %s with fnm, the system Node.js will be used.",
            node_version_spec,
        )
        return
    # Resolve the installation directory, the specification can be an alias like `lts/*`.
    command = ["fnm", "exec", f"--using={node_version_spec}", "--", "node", "-p", "process.execPath"]
    stdout, success, _ = await module_utils.run_timeout(
        command,
        env,
        _TIMEOUT_SUBPROCESS,
        "Resolve the Node.js installation directory",
        "Error while resolving the Node.js installation directory",
        "Timeout while resolving the Node.js installation directory",
        cwd,
    )
    if not success or not stdout or not stdout.strip():
        _LOGGER.warning(
            "Unable to resolve the Node.js version %s installed with fnm, the system Node.js will be used.",
            node_version_spec,
        )
        return
    node_bin = str(Path(stdout.strip()).parent)
    env["PATH"] = f"{node_bin}:{env['PATH']}"
    _LOGGER.info("Using the Node.js version %s from %s.", node_version_spec, node_bin)


async def _install_requirements_dependencies(
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    result: list[module_utils.Message],
    env: dict[str, str],
    cwd: anyio.Path,
) -> None:
    command = ["git", "ls-files", "requirements.txt", "*/requirements.txt"]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    async with asyncio.timeout(_TIMEOUT_GIT_LSFILES.total_seconds()):
        stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        message = module_utils.AnsiProcessMessage.from_async_artifacts(command, proc, stdout, stderr)
        message.title = "Error in ls-files"
        _LOGGER.warning(message)
        result.append(message)
    else:
        for file in stdout.decode().strip().split("\n"):
            if not file:
                continue
            if file in local_config.get("files-no-install", config.get("files-no-install", [])):
                continue

            _, _, proc_message = await module_utils.run_timeout(
                [
                    "python",
                    "-m",
                    "pip",
                    "install",
                    *local_config.get("pip-install-arguments", config.get("pip-install-arguments", [])),
                    f"--requirement={file}",
                ],
                env,
                _TIMEOUT_PYTHON_INSTALL,
                f"Dependencies installed from {file}",
                f"Error while installing the dependencies from {file}",
                f"Timeout while installing the dependencies from {file}",
                cwd,
            )
            if proc_message is not None:
                result.append(proc_message)


async def _install_pipenv_dependencies(
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    result: list[module_utils.Message],
    env: dict[str, str],
    cwd: anyio.Path,
) -> None:
    command = ["git", "ls-files", "Pipfile", "*/Pipfile"]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    async with asyncio.timeout(_TIMEOUT_GIT_LSFILES.total_seconds()):
        stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        message = module_utils.AnsiProcessMessage.from_async_artifacts(command, proc, stdout, stderr)
        message.title = "Error in ls-files"
        _LOGGER.warning(message)
        result.append(message)
    else:
        for file in stdout.decode().strip().split("\n"):
            if not file:
                continue
            if file in local_config.get("files-no-install", config.get("files-no-install", [])):
                continue
            directory = (await (cwd / file).resolve()).parent

            _, _, proc_message = await module_utils.run_timeout(
                [
                    "pipenv",
                    "sync",
                    *local_config.get("pipenv-sync-arguments", config.get("pipenv-sync-arguments", [])),
                ],
                env,
                _TIMEOUT_PYTHON_INSTALL,
                f"Dependencies installed from {file}",
                f"Error while installing the dependencies from {file}",
                f"Timeout while installing the dependencies from {file}",
                directory,
            )
            if proc_message is not None:
                result.append(proc_message)


async def _install_poetry_dependencies(
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    result: list[module_utils.Message],
    env: dict[str, str],
    cwd: anyio.Path,
) -> list[anyio.Path]:
    install_dirs: list[anyio.Path] = []
    command = ["git", "ls-files", "poetry.lock", "*/poetry.lock"]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    async with asyncio.timeout(_TIMEOUT_GIT_LSFILES.total_seconds()):
        stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        message = module_utils.AnsiProcessMessage.from_async_artifacts(command, proc, stdout, stderr)
        message.title = "Error in ls-files"
        _LOGGER.warning(message)
        result.append(message)
    else:
        for file in stdout.decode().strip().split("\n"):
            if not file:
                continue
            if file in local_config.get("files-no-install", config.get("files-no-install", [])):
                continue

            install_dir = (await (cwd / file).resolve()).parent
            _, _, proc_message = await module_utils.run_timeout(
                [
                    "poetry",
                    "install",
                    *local_config.get("poetry-install-arguments", config.get("poetry-install-arguments", [])),
                ],
                env,
                _TIMEOUT_PYTHON_INSTALL,
                f"Dependencies installed from {file}",
                f"Error while installing the dependencies from {file}",
                f"Timeout while installing the dependencies from {file}",
                install_dir,
            )
            install_dirs.append(install_dir)
            if proc_message is not None:
                result.append(proc_message)
    return install_dirs


async def _cleanup_poetry_envs(
    install_dirs: list[anyio.Path],
    env: dict[str, str],
) -> None:
    for install_dir in install_dirs:
        await module_utils.run_timeout(
            ["poetry", "env", "remove", "python"],
            env,
            settings.audit.timeouts.poetry_env_remove,
            success_message=f"Poetry virtual environment removed in {install_dir}",
            error_message=f"Failed to remove poetry virtual environment in {install_dir}",
            timeout_message=f"Poetry virtual environment removal timed out in {install_dir}",
            cwd=install_dir,
            error=False,
        )


async def _snyk_monitor(
    branch: str,
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    result: list[module_utils.Message],
    env: dict[str, str],
    cwd: anyio.Path,
) -> bool:
    command = [
        "snyk",
        "monitor",
        f"--target-reference={branch}",
        *local_config.get(
            "monitor-arguments",
            config.get("monitor-arguments", configuration.SNYK_MONITOR_ARGUMENTS_DEFAULT),
        ),
    ]
    local_monitor_config = local_config.get("monitor", {})
    monitor_config = config.get("monitor", {})
    if "project-environment" in local_monitor_config or "project-environment" in monitor_config:
        command.append(
            f"--project-environment={','.join(local_monitor_config.get('project-environment', monitor_config.get('project-environment', [])))}",
        )
    if "project-lifecycle" in local_monitor_config or "project-lifecycle" in monitor_config:
        command.append(
            f"--project-lifecycle={','.join(local_monitor_config.get('project-lifecycle', monitor_config.get('project-lifecycle', [])))}",
        )
    if (
        "project-business-criticality" in local_monitor_config
        or "project-business-criticality" in monitor_config
    ):
        command.append(
            f"--project-business-criticality={','.join(local_monitor_config.get('project-business-criticality', monitor_config.get('project-business-criticality', [])))}",
        )
    if "project-tags" in local_monitor_config or "project-tags" in monitor_config:
        command.append(
            f"--project-tags={','.join(['='.join(tag) for tag in local_monitor_config.get('project-tags', monitor_config.get('project-tags', {}))])}",
        )

    _, success, message = await module_utils.run_timeout(
        command,
        env,
        _TIMEOUT_SNYK,
        "Project monitored",
        "Error while monitoring the project",
        "Timeout while monitoring the project",
        cwd,
    )
    if message is not None:
        result.append(message)
    return success


_SNYK_API_VERSION = "2024-05-31"
"""The Snyk REST API version used for the projects cleanup."""

_SNYK_MONITOR_SKEW = dt.timedelta(minutes=10)
"""Tolerance applied on the monitor run start when looking for stale projects (clock desynchronization)."""

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


class _SnykApiError(Exception):
    """Raised when a Snyk REST API call fails, the cleanups are best-effort and report it."""

    def __init__(self, url: str, status: int | None = None, detail: str = "") -> None:
        self.url = url
        self.status = status
        self.detail = detail[:500]
        super().__init__(" ".join(part for part in (url, str(status) if status else "", self.detail) if part))


def _snyk_api_datetime(moment: dt.datetime) -> str:
    """
    Format a datetime for the Snyk REST API query parameters.

    The `Z` UTC suffix is used instead of the `+00:00` ISO offset because the `+` is
    rejected by the API query parameters validation (SNYK-OPENAPI-0001, 400 Bad request).
    """
    return moment.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _snyk_api_config() -> tuple[str, str] | None:
    """Get the Snyk REST API configuration, or None when the cleanup is disabled or not configured."""
    if not settings.audit.snyk_api_cleanup:
        _LOGGER.debug("The Snyk REST API cleanup is disabled")
        return None
    if not settings.audit.snyk_token:
        _LOGGER.info("No Snyk API token configured, skip the Snyk projects cleanup")
        return None
    return settings.audit.snyk_token, settings.audit.snyk_api_url


async def _snyk_api_get(
    session: aiohttp.ClientSession,
    url: str,
    params: dict[str, str] | list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    """
    Perform a GET request on the Snyk REST API.

    Raises `_SnykApiError` on failure, the callers report it without failing the audit job.
    """
    try:
        async with session.get(url, params=params) as response:
            if not response.ok:
                detail = await response.text()
                _LOGGER.warning("Snyk API error on %s: %s %s", url, response.status, detail[:500])
                raise _SnykApiError(url, response.status, detail)
            data: dict[str, Any] = json.loads(await response.read())
            return data
    except (aiohttp.ClientError, TimeoutError) as error:
        _LOGGER.warning("Snyk API request error on %s: %s", url, error)
        raise _SnykApiError(url, None, str(error)) from error


async def _snyk_api_get_all(
    session: aiohttp.ClientSession,
    api_url: str,
    path: str,
    params: dict[str, str] | list[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Get all the entries of a paginated Snyk REST API collection, raises `_SnykApiError` on failure."""
    entries: list[dict[str, Any]] = []
    url: str | None = f"{api_url}/rest{path}"
    request_params: dict[str, str] | list[tuple[str, str]] | None = [
        *(params.items() if isinstance(params, dict) else params or []),
        ("version", _SNYK_API_VERSION),
        ("limit", "100"),
    ]
    while url is not None:
        data = await _snyk_api_get(session, url, request_params)
        entries.extend(data.get("data", []))
        next_url = (data.get("links") or {}).get("next")
        if not next_url:
            break
        url = str(next_url) if str(next_url).startswith("http") else f"{api_url}{next_url}"
        # The next link already contains the query parameters
        request_params = None
    return entries


async def _resolve_snyk_org_id(session: aiohttp.ClientSession, api_url: str) -> str | None:
    """Resolve the configured Snyk organization (UUID or slug) to its UUID."""
    org = settings.audit.snyk_org
    orgs: list[dict[str, Any]] = []
    if not org:
        # No organization configured: use the single organization accessible with the token
        orgs = await _snyk_api_get_all(session, api_url, "/orgs")
        if len(orgs) == 1:
            attributes = orgs[0].get("attributes") or {}
            _LOGGER.info(
                "Using the single Snyk organization accessible with the token: %s",
                attributes.get("slug") or orgs[0]["id"],
            )
            return str(orgs[0]["id"])
        _LOGGER.warning(
            "No Snyk organization configured and %s organizations are accessible with the token, "
            "set the snyk_org setting (SNYK_ORG) to select one, skip the Snyk projects cleanup",
            len(orgs),
        )
        return None
    if _UUID_RE.match(org):
        return org
    orgs = await _snyk_api_get_all(session, api_url, "/orgs")
    for entry in orgs:
        attributes = entry.get("attributes") or {}
        if org in (attributes.get("slug"), attributes.get("name")):
            return str(entry["id"])
    _LOGGER.warning("The Snyk organization %s was not found through the API", org)
    return None


async def _resolve_snyk_target_ids(
    session: aiohttp.ClientSession,
    api_url: str,
    org_id: str,
    owner: str,
    repository: str,
) -> list[str]:
    """Find the Snyk target IDs of a Git repository."""
    repo_path = f"{owner}/{repository}".lower()
    targets = await _snyk_api_get_all(session, api_url, f"/orgs/{org_id}/targets")
    target_ids = []
    for target in targets:
        attributes = target.get("attributes") or {}
        display_name = str(attributes.get("display_name") or "").lower()
        if display_name == repo_path or display_name.endswith(
            (f"/{repo_path}", f"{repo_path}.git", f":{repo_path}", f":{repo_path}.git")
        ):
            target_ids.append(str(target["id"]))
    if not target_ids:
        _LOGGER.info("No Snyk target found for the repository %s, skip the Snyk projects cleanup", repo_path)
    return target_ids


async def _snyk_resolve_targets(
    session: aiohttp.ClientSession,
    api_url: str,
    owner: str,
    repository: str,
) -> tuple[str, list[str]] | None:
    """Resolve the Snyk organization and the target IDs of a repository, None when not resolvable."""
    org_id = await _resolve_snyk_org_id(session, api_url)
    if org_id is None:
        return None
    target_ids = await _resolve_snyk_target_ids(session, api_url, org_id, owner, repository)
    if not target_ids:
        return None
    return org_id, target_ids


async def _snyk_list_projects(
    session: aiohttp.ClientSession,
    api_url: str,
    org_id: str,
    target_ids: list[str],
    extra_params: list[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """List the Snyk projects of the given targets."""
    return await _snyk_api_get_all(
        session,
        api_url,
        f"/orgs/{org_id}/projects",
        [
            *[("target_id", target_id) for target_id in target_ids],
            *(extra_params or []),
        ],
    )


async def _snyk_delete_projects(
    session: aiohttp.ClientSession,
    api_url: str,
    org_id: str,
    projects: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Delete Snyk projects, returns the successfully deleted ones and the number of failures."""

    async def delete_one(project: dict[str, Any]) -> dict[str, Any] | None:
        project_id = str(project["id"])
        url = f"{api_url}/rest/orgs/{org_id}/projects/{project_id}"
        try:
            async with session.delete(url, params={"version": _SNYK_API_VERSION}) as response:
                if response.ok:
                    _LOGGER.debug("Snyk project %s deleted", project_id)
                    return project
                # A deletion failure on one project must not prevent the cleanup of the others
                _LOGGER.warning(
                    "Failed to delete the Snyk project %s: %s %s",
                    project_id,
                    response.status,
                    (await response.text())[:200],
                )
        except (aiohttp.ClientError, TimeoutError):
            # A network error on one project must not prevent the cleanup of the others
            _LOGGER.warning("Failed to delete the Snyk project %s", project_id, exc_info=True)
        return None

    deleted = await asyncio.gather(*(delete_one(project) for project in projects))
    successfully_deleted = [project for project in deleted if project is not None]
    return successfully_deleted, len(deleted) - len(successfully_deleted)


def _snyk_project_description(project: dict[str, Any]) -> str:
    """Build a short description of a Snyk project for the reports."""
    attributes = project.get("attributes") or {}
    name = str(attributes.get("name") or project["id"])
    target_file = attributes.get("target_file")
    return f"{name} [{target_file}]" if target_file else name


def _snyk_api_session(token: str) -> aiohttp.ClientSession:
    """Build an authenticated aiohttp session for the Snyk REST API."""
    return aiohttp.ClientSession(
        headers={"Authorization": f"token {token}"},
        timeout=aiohttp.ClientTimeout(total=settings.audit.timeouts.snyk_api.total_seconds()),
    )


async def snyk_cleanup_stale_projects(
    owner: str,
    repository: str,
    branch: str,
    monitored_before: dt.datetime,
    result: list[module_utils.Message],
) -> bool:
    """
    Delete the Snyk projects of a reference that were not refreshed by the latest monitor run.

    This removes the projects of the dependency files that are not scanned anymore,
    a reference grouping disappears from the Snyk UI when it does not contain any project.
    Returns False when the cleanup could not be performed (Snyk API error).
    """
    api_config = _snyk_api_config()
    if api_config is None:
        return True
    token, api_url = api_config
    try:
        async with _snyk_api_session(token) as session:
            targets = await _snyk_resolve_targets(session, api_url, owner, repository)
            if targets is None:
                return True
            org_id, target_ids = targets
            projects = await _snyk_list_projects(
                session,
                api_url,
                org_id,
                target_ids,
                [
                    ("target_reference", branch),
                    ("cli_monitored_before", _snyk_api_datetime(monitored_before)),
                ],
            )
            stale_projects = [
                project for project in projects if (project.get("attributes") or {}).get("origin") == "cli"
            ]
            if not stale_projects:
                _LOGGER.debug("No stale Snyk project to remove for the reference %s", branch)
                return True
            deleted, failures = await _snyk_delete_projects(session, api_url, org_id, stale_projects)
            if deleted:
                _LOGGER.info(
                    "Removed %s stale Snyk project(s) of the reference %s",
                    len(deleted),
                    branch,
                )
                message = module_utils.HtmlMessage(
                    f"Removed {len(deleted)} stale Snyk project(s) of the reference {html.escape(branch)}: "
                    + html.escape(", ".join(_snyk_project_description(project) for project in deleted))
                )
                message.title = "Snyk projects cleanup"
                result.append(message)
            return failures == 0
    except _SnykApiError as error:
        # The cleanup is best-effort: report the error instead of failing the audit job
        _LOGGER.exception("The Snyk stale projects cleanup failed for %s/%s (%s)", owner, repository, branch)
        message = module_utils.HtmlMessage(html.escape(f"Snyk projects cleanup failed: {error}"))
        message.title = "Snyk projects cleanup"
        result.append(message)
        return False


async def snyk_cleanup_removed_references(
    owner: str, repository: str, known_versions: list[str]
) -> tuple[list[str], bool]:
    """
    Delete all the Snyk projects whose target reference is not a supported version anymore.

    Emptying a reference makes it disappear from the Snyk UI, returns the report entries
    of the removed references and False when the cleanup could not be performed (Snyk API error).
    """
    api_config = _snyk_api_config()
    if api_config is None:
        return [], True
    token, api_url = api_config
    try:
        async with _snyk_api_session(token) as session:
            targets = await _snyk_resolve_targets(session, api_url, owner, repository)
            if targets is None:
                return [], True
            org_id, target_ids = targets
            projects = await _snyk_list_projects(session, api_url, org_id, target_ids)
            removed_projects = [
                project
                for project in projects
                if (project.get("attributes") or {}).get("origin") == "cli"
                and (project.get("attributes") or {}).get("target_reference") not in known_versions
            ]
            if not removed_projects:
                _LOGGER.debug("No Snyk reference to remove for the repository %s/%s", owner, repository)
                return [], True
            deleted, failures = await _snyk_delete_projects(session, api_url, org_id, removed_projects)
            references: dict[str, int] = {}
            for project in deleted:
                reference = str((project.get("attributes") or {}).get("target_reference") or "")
                references[reference] = references.get(reference, 0) + 1
            return (
                [
                    f"Snyk reference `{reference}` ({number} projects)"
                    for reference, number in sorted(references.items())
                ],
                failures == 0,
            )
    except _SnykApiError:
        # The cleanup is best-effort: report the error instead of failing the audit job
        _LOGGER.exception("The Snyk references cleanup failed for %s/%s", owner, repository)
        return [], False


async def snyk_cleanup_stale_projects_by_age(owner: str, repository: str) -> tuple[list[str], bool]:
    """
    Delete the Snyk projects of the repository that were not re-monitored for too long.

    This removes, independently of the monitor runs result, the leftovers of dependency
    files that are not scanned anymore (e.g. projects created with random names by past
    runs) and the projects of references whose monitor fails for a long time.
    Returns the report entries of the removed projects and False when the cleanup
    could not be performed (Snyk API error).
    """
    api_config = _snyk_api_config()
    if api_config is None:
        return [], True
    token, api_url = api_config
    stale_age = settings.audit.snyk_api_stale_age
    try:
        async with _snyk_api_session(token) as session:
            targets = await _snyk_resolve_targets(session, api_url, owner, repository)
            if targets is None:
                return [], True
            org_id, target_ids = targets
            monitored_before = dt.datetime.now(dt.UTC) - stale_age
            projects = await _snyk_list_projects(
                session,
                api_url,
                org_id,
                target_ids,
                [("cli_monitored_before", _snyk_api_datetime(monitored_before))],
            )
            stale_projects = [
                project for project in projects if (project.get("attributes") or {}).get("origin") == "cli"
            ]
            if not stale_projects:
                _LOGGER.debug(
                    "No stale Snyk project by age to remove for the repository %s/%s", owner, repository
                )
                return [], True
            deleted, failures = await _snyk_delete_projects(session, api_url, org_id, stale_projects)
            if not deleted:
                return [], failures == 0
            _LOGGER.info(
                "Removed %s stale Snyk project(s) of %s/%s not monitored since %s days",
                len(deleted),
                owner,
                repository,
                stale_age.days,
            )
            return [f"{len(deleted)} stale Snyk project(s) not monitored since {stale_age.days} days"], (
                failures == 0
            )
    except _SnykApiError:
        # The cleanup is best-effort: report the error instead of failing the audit job
        _LOGGER.exception("The Snyk stale projects by age cleanup failed for %s/%s", owner, repository)
        return [], False


async def _snyk_test(
    branch: str,
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    result: list[module_utils.Message],
    env_no_debug: dict[str, str],
    cwd: anyio.Path,
    ignore_policy: bool = False,
) -> tuple[
    dict[str, int],
    dict[str, int],
    dict[str, str],
    dict[str, set[str]],
    bool,
    dict[str, list[VulnerabilityData]],
]:
    # Test with human output
    if not ignore_policy:
        command = [
            "snyk",
            "test",
            *local_config.get(
                "test-arguments",
                config.get("test-arguments", configuration.SNYK_TEST_ARGUMENTS_DEFAULT),
            ),
        ]
        await module_utils.run_timeout(
            command,
            env_no_debug,
            _TIMEOUT_SNYK,
            "Snyk test (human)",
            "Error while testing the project",
            "Timeout while testing the project",
            cwd,
        )

    command = [
        "snyk",
        "test",
        "--json",
        *local_config.get(
            "test-arguments",
            config.get("test-arguments", configuration.SNYK_TEST_ARGUMENTS_DEFAULT),
        ),
        *(["--ignore-policy"] if ignore_policy else []),
    ]
    test_json_str, _, message = await module_utils.run_timeout(
        command,
        env_no_debug,
        _TIMEOUT_SNYK,
        "Snyk test",
        "Error while testing the project",
        "Timeout while testing the project",
        cwd,
    )
    if message is not None:
        result.append(message)

    if test_json_str:
        message = module_utils.HtmlMessage(utils.format_json_str(test_json_str[:10000]))
        message.title = "Snyk test JSON output"
        _LOGGER.debug(message)
    else:
        _LOGGER.error(
            "Snyk test JSON returned nothing on project %s branch %s",
            module_utils.get_cwd(),
            branch,
        )

    test_json = json.loads(test_json_str) if test_json_str else []

    if not isinstance(test_json, list):
        test_json = [test_json]

    _LOGGER.debug("Start parsing the vulnerabilities")
    high_vulnerabilities: dict[str, int] = {}
    fixable_vulnerabilities: dict[str, int] = {}
    fixable_vulnerabilities_summary: dict[str, str] = {}
    fixable_files_npm: dict[str, set[str]] = {}
    vulnerabilities_in_requirements = False
    file_vulnerabilities: dict[str, list[VulnerabilityData]] = {}
    for row in test_json:
        if "error" in row:
            _LOGGER.error(row["error"])
            continue

        message = module_utils.HtmlMessage(
            "\n".join(
                [
                    f"Package manager: {row.get('packageManager', '-')}",
                    f"Target file: {row.get('displayTargetFile', '-')}",
                    f"Project path: {row.get('path', '-')}",
                    row.get("summary", ""),
                ],
            ),
        )
        message.title = f"{row.get('summary', 'Snyk test')} in {row.get('displayTargetFile', '-')}."
        _LOGGER.info(message)

        package_manager = row.get("packageManager")

        for vuln in row.get("vulnerabilities", []):
            fixable = vuln.get("fixedIn", []) or vuln.get("isPatchable", False)
            severity = vuln["severity"]
            display = False
            if fixable:
                fixable_vulnerabilities[severity] = fixable_vulnerabilities.get(severity, 0) + 1
                display = True
            if severity in ("high", "critical"):
                high_vulnerabilities[severity] = high_vulnerabilities.get(severity, 0) + 1
                display = True
            if not display:
                continue
            severity = vuln["severity"]
            title = " ".join(
                [
                    f"[{severity.upper()}]",
                    f"{vuln['packageName']}@{vuln['version']}:",
                    vuln["id"],
                    *(vuln.get("identifiers", {}).get("CWE", [])),
                ],
            )
            if vuln.get("fixedIn", []):
                title += " [Fixed in: " + ", ".join(vuln["fixedIn"]) + "]."
            elif vuln.get("isUpgradable", False):
                title += " [Upgradable]."
            elif vuln.get("isPatchable", False):
                title += " [Patch available]."
            else:
                title += "."
            if vuln.get("fixedIn", []) or vuln.get("isUpgradable", False) or vuln.get("isPatchable", False):
                fixable_vulnerabilities_summary[vuln["id"]] = title
                if vuln.get("packageManager") == "npm":
                    fixable_files_npm.setdefault(row.get("displayTargetFile"), set()).add(title)
            elif package_manager == "pip":
                vulnerabilities_in_requirements = True

            target_file = row.get("displayTargetFile", "-")
            cve_ids = vuln.get("identifiers", {}).get("CVE", [])
            cwe_ids = vuln.get("identifiers", {}).get("CWE", [])
            vuln_data = VulnerabilityData(
                file=target_file,
                package_name=vuln["packageName"],
                package_version=vuln["version"],
                package_manager=vuln.get("packageManager", ""),
                severity=vuln["severity"],
                snyk_id=vuln["id"],
                cve_ids=cve_ids,
                cwe_ids=cwe_ids,
                title=title,
                fixed_in=vuln.get("fixedIn", []),
                is_upgradable=vuln.get("isUpgradable", False),
                is_patchable=vuln.get("isPatchable", False),
            )
            existing_vulns = file_vulnerabilities.setdefault(target_file, [])
            if not any(
                v.snyk_id == vuln_data.snyk_id and v.package_version == vuln_data.package_version
                for v in existing_vulns
            ):
                existing_vulns.append(vuln_data)

    _LOGGER.debug("End parsing the vulnerabilities")
    return (
        high_vulnerabilities,
        fixable_vulnerabilities,
        fixable_vulnerabilities_summary,
        fixable_files_npm,
        vulnerabilities_in_requirements,
        file_vulnerabilities,
    )


async def _snyk_fix(
    branch: str,
    cwd: anyio.Path,
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    logs_url: str,
    result: list[module_utils.Message],
    env_no_debug: dict[str, str],
    env_debug: dict[str, str],
    fixable_vulnerabilities_summary: dict[str, str],
    vulnerabilities_in_requirements: bool,
) -> tuple[bool, module_utils.HtmlMessage | None]:
    await module_utils.run_timeout(
        ["poetry", "--version"],
        os.environ.copy(),
        _TIMEOUT_POETRY_VERSION,
        "Poetry version",
        "Error while getting the Poetry version",
        "Timeout while getting the Poetry version",
        cwd,
        error=False,
    )

    snyk_fix_success = True
    snyk_fix_message = None
    command = ["git", "reset", "--hard"]
    proc = await asyncio.create_subprocess_exec(*command, cwd=cwd)
    async with asyncio.timeout(settings.audit.timeouts.git_reset_hard.total_seconds()):
        await proc.communicate()
    if fixable_vulnerabilities_summary or vulnerabilities_in_requirements:
        command = [
            "snyk",
            "fix",
            *local_config.get(
                "fix-arguments",
                config.get("fix-arguments", configuration.SNYK_FIX_ARGUMENTS_DEFAULT),
            ),
        ]
        fix_message, snyk_fix_success, message = await module_utils.run_timeout(
            command,
            env_no_debug,
            _TIMEOUT_SNYK_FIX,
            "Snyk fix",
            "Error while fixing the project",
            "Timeout while fixing the project",
            cwd,
        )
        if message is not None:
            result.append(message)
        if fix_message:
            snyk_fix_message = module_utils.AnsiMessage(fix_message.strip())
            snyk_fix_message.title = "snyk fix output"
        if not snyk_fix_success:
            await module_utils.run_timeout(
                command,
                env_debug,
                _TIMEOUT_SNYK,
                "Snyk fix (debug)",
                "Error while fixing the project (debug)",
                "Timeout while fixing the project (debug)",
                cwd,
            )

            project = "-" if cwd is None else cwd.name
            message = module_utils.HtmlMessage(
                "<br>\n".join(
                    [
                        *fixable_vulnerabilities_summary.values(),
                        f"Project: {project}:{branch}",
                        f"See logs: {logs_url}",
                    ],
                ),
            )
            message.title = f"Unable to fix {len(fixable_vulnerabilities_summary)} vulnerabilities"
            _LOGGER.warning(message)
            result.append(message)

    return snyk_fix_success, snyk_fix_message


async def _npm_audit_fix(
    fixable_files_npm: dict[str, set[str]],
    result: list[module_utils.Message],
    cwd: anyio.Path,
    env: dict[str, str],
) -> tuple[str, bool]:
    messages: set[str] = set()
    fix_success = True
    for package_lock_file_name, file_messages in fixable_files_npm.items():
        directory = (await (cwd / package_lock_file_name).absolute()).parent
        messages.update(file_messages)
        _LOGGER.debug("Fixing vulnerabilities in %s with npm audit fix", package_lock_file_name)
        command = ["npm", "audit", "fix"]
        _, success, message = await module_utils.run_timeout(
            command,
            env,
            _TIMEOUT_NPM_AUDIT,
            "Npm audit fix",
            "Error while fixing the project",
            "Timeout while fixing the project",
            directory,
        )
        if message is not None:
            result.append(message)
        _LOGGER.debug("Fixing version in %s", package_lock_file_name)
        # Remove the add '~' in the version in the package.json
        async with await anyio.open_file(directory / "package.json", encoding="utf-8") as package_file:
            package_json = json.load(io.StringIO(await package_file.read()))
            for dependencies_type in ("dependencies", "devDependencies"):
                for package, version in package_json.get(dependencies_type, {}).items():
                    if version.startswith("^"):
                        package_json[dependencies_type][package] = version[1:]
        async with await anyio.open_file(directory / "package.json", "w", encoding="utf-8") as package_file:
            string_io = io.StringIO()
            json.dump(package_json, string_io, indent=2)
            await package_file.write(string_io.getvalue())
        _LOGGER.debug("Succeeded fix %s", package_lock_file_name)

        fix_success &= success
    return "\n".join(messages), fix_success


def outdated_versions(
    security: security_md.Security,
) -> list[str | models.OutputData]:
    """Check that the versions from the SECURITY.md are not outdated."""
    version_index = security.headers.index("Version")
    date_index = security.headers.index("Supported Until")

    errors: list[str | models.OutputData] = []

    for row in security.data:
        str_date = row[date_index]
        if str_date not in ("Unsupported", "Best effort", "To be defined"):
            date = dt.datetime.strptime(row[date_index], "%d/%m/%Y").replace(tzinfo=dt.UTC)
            if date < dt.datetime.now(dt.UTC):
                errors.append(
                    f"The version '{row[version_index]}' is outdated, it can be set to "
                    "'Unsupported', 'Best effort' or 'To be defined'",
                )
    return errors


_GENERATION_TIME = None
_SOURCES: dict[str, apt_repo.APTSources] = {}
_PACKAGE_VERSION: dict[str, debian_inspector.version.Version] = {}


def _get_sources(
    dist: str,
    config: configuration.DpkgConfiguration,
    local_config: configuration.DpkgConfiguration,
) -> apt_repo.APTSources:
    """Get the sources for the distribution."""
    if dist not in _SOURCES:
        conf = local_config.get("sources", config.get("sources", configuration.DPKG_SOURCES_DEFAULT))
        if dist not in conf:
            message = f"The distribution {dist} is not in the configuration"
            raise ValueError(message)
        _SOURCES[dist] = apt_repo.APTSources(
            [
                apt_repo.APTRepository(
                    source["url"],
                    source["distribution"],
                    source["components"],
                )
                for source in conf[dist]
            ],
        )
        try:
            for package in _SOURCES[dist].packages:
                name = f"{dist}/{package.package}"
                try:
                    version = debian_inspector.version.Version.from_string(package.version)
                    if name not in _PACKAGE_VERSION or version > _PACKAGE_VERSION[name]:
                        _PACKAGE_VERSION[name] = version
                except ValueError as exception:
                    _LOGGER.warning(
                        "Error while parsing the package %s/%s version of %s: %s",
                        dist,
                        package.package,
                        package.version,
                        exception,
                    )
        except AttributeError as exception:
            _LOGGER.error("Error while loading the distribution %s: %s", dist, exception)  # noqa: TRY400

    return _SOURCES[dist]


async def _get_packages_version(
    package: str,
    config: configuration.DpkgConfiguration,
    local_config: configuration.DpkgConfiguration,
) -> str | None:
    """Get the version of the package."""
    global _GENERATION_TIME  # noqa: PLW0603
    if (
        _GENERATION_TIME is None
        or dt.datetime.now(dt.UTC) - settings.audit.dpkg_cache_duration > _GENERATION_TIME
    ):
        _PACKAGE_VERSION.clear()
        _SOURCES.clear()
        _GENERATION_TIME = dt.datetime.now(dt.UTC)
    if package not in _PACKAGE_VERSION:
        dist = package.split("/", maxsplit=1)[0]
        await asyncio.to_thread(_get_sources, dist, config, local_config)
    if package not in _PACKAGE_VERSION:
        _LOGGER.warning("No version found for %s", package)
        return None
    return str(_PACKAGE_VERSION[package])


async def dpkg(
    config: configuration.DpkgConfiguration,
    local_config: configuration.DpkgConfiguration,
    cwd: anyio.Path,
) -> None:
    """Update the version of packages in the file .github/dpkg-versions.yaml or ci/dpkg-versions.yaml."""
    ci_dpkg_versions_filename = cwd / ".github" / "dpkg-versions.yaml"
    github_dpkg_versions_filename = cwd / "ci" / "dpkg-versions.yaml"

    if not await ci_dpkg_versions_filename.exists() and not await github_dpkg_versions_filename.exists():
        _LOGGER.warning("The file .github/dpkg-versions.yaml or ci/dpkg-versions.yaml does not exist")

    dpkg_versions_filename = (
        github_dpkg_versions_filename
        if await github_dpkg_versions_filename.exists()
        else ci_dpkg_versions_filename
    )

    versions_config = yaml.load(
        await dpkg_versions_filename.read_text(encoding="utf-8"),
        Loader=yaml.SafeLoader,
    )
    for versions in versions_config.values():
        for package_full in versions:
            version = await _get_packages_version(package_full, config, local_config)
            if version is None:
                _LOGGER.warning("No version found for %s", package_full)
                continue
            if versions[package_full] is None or versions[package_full] == "None":
                versions[package_full] = version
                continue
            try:
                current_version = debian_inspector.version.Version.from_string(versions[package_full])
            except ValueError as exception:
                _LOGGER.warning(
                    "Error while parsing the current version '%s' of the package %s: %s",
                    versions[package_full],
                    package_full,
                    exception,
                )
                versions[package_full] = version
                continue
            try:
                if debian_inspector.version.Version.from_string(version) > current_version:
                    versions[package_full] = version
            except ValueError as exception:
                _LOGGER.warning(
                    "Error while parsing the new version '%s' of the package %s: %s",
                    version,
                    package_full,
                    exception,
                )

    await dpkg_versions_filename.write_text(
        yaml.dump(versions_config, Dumper=yaml.SafeDumper),
        encoding="utf-8",
    )


async def find_snyk_files(cwd: anyio.Path) -> list[anyio.Path]:
    """Find all .snyk files in the repository."""
    command = ["git", "ls-files", "**/.snyk"]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    async with asyncio.timeout(settings.audit.timeouts.snyk_files.total_seconds()):
        stdout, _ = await proc.communicate()
    result = stdout.decode().strip()
    return [cwd / f for f in result.split("\n") if f] if result else []


async def parse_snyk_ignore_reasons(snyk_file: anyio.Path) -> dict[str, str]:
    """Parse a .snyk file and return a dict mapping Snyk ID to ignore reason."""
    if not await snyk_file.exists():
        return {}
    data = yaml.safe_load(await snyk_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    ignore = data.get("ignore", {})
    if not isinstance(ignore, dict):
        return {}
    reasons: dict[str, str] = {}
    for snyk_id, entries in ignore.items():
        if isinstance(entries, list) and entries:
            entry = entries[0]
            if isinstance(entry, dict):
                for details in entry.values():
                    if isinstance(details, dict) and "reason" in details:
                        reasons[str(snyk_id)] = details["reason"]
                        break
    return reasons


async def snyk_test_ignored(
    branch: str,
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    env: dict[str, str],
    cwd: anyio.Path,
) -> dict[str, list[VulnerabilityData]]:
    """Run snyk test --json --ignore-policy and return file-grouped vulnerability data."""
    env_no_debug = {**env}
    result: list[module_utils.Message] = []
    _, _, _, _, _, file_vulnerabilities = await _snyk_test(
        branch,
        config,
        local_config,
        result,
        env_no_debug,
        cwd,
        ignore_policy=True,
    )
    return file_vulnerabilities


class TrivyVulnerability(BaseModel):
    """A single vulnerability reported by Trivy for a container image."""

    cve: str
    """The CVE identifier (or the Trivy vulnerability ID when no CVE is known)."""
    package: str
    """The vulnerable package name."""
    installed_version: str
    """The installed vulnerable version in the image."""
    fixed_version: str = ""
    """The version that fixes the vulnerability, empty when there is no fix yet."""
    severity: str = "critical"
    """The vulnerability severity (lowercase)."""
    title: str = ""
    """A short description of the vulnerability."""
    references: list[str] = []
    """The reference URLs of the vulnerability."""


class TrivyImage(BaseModel):
    """The Trivy scan result of a single container image."""

    image: str
    """The container image reference (``repository:tag`` or ``repository@sha256:...``)."""
    critical_count: int = 0
    """Number of vulnerabilities at or above the configured severity threshold."""
    vulnerabilities: list[TrivyVulnerability] = []
    """The reported vulnerabilities."""
    scan_error: str = ""
    """The scan failure reason, empty when the image was scanned successfully."""
    via_sbom: bool = False
    """True when the scan was performed on the downloaded SBOM attestation instead of the image."""


class TrivyApplication(BaseModel):
    """The container images used by a single ArgoCD application."""

    application: str
    """The application directory path, relative to the repository root."""
    images: list[TrivyImage] = []
    """The images referenced by the application."""


class TrivyReport(BaseModel):
    """The aggregated result of the container images scan of a repository branch."""

    branch: str
    applications: list[TrivyApplication] = []
    image_count: int = 0
    """Number of distinct images scanned (an image used by several applications is counted once)."""
    critical_total: int = 0
    """Total number of reported vulnerabilities across the distinct scanned images."""
    scan_errors: int = 0
    """Number of images that could not be scanned."""


class TrivyPublishedImage(BaseModel):
    """The Trivy scan result of a single published container image, keyed by its digest."""

    repository: str
    """The image repository without tag (``host/name``)."""
    digest: str
    """The scanned manifest digest (``sha256:...``), empty when no digest could be resolved."""
    tags: list[str] = []
    """The published tags pointing to this digest (the floating tags are grouped here)."""
    versions: list[str] = []
    """The supported versions from which this image is published."""
    critical_count: int = 0
    """Number of vulnerabilities at or above the configured severity threshold."""
    vulnerabilities: list[TrivyVulnerability] = []
    """The reported vulnerabilities."""
    scan_error: str = ""
    """The scan failure reason, empty when the image was scanned successfully."""
    via_sbom: bool = False
    """True when the scan was performed on the downloaded SBOM attestation instead of the image."""


class TrivyPublishedReport(BaseModel):
    """The aggregated result of the published container images scan of a repository."""

    branch: str
    images: list[TrivyPublishedImage] = []
    image_count: int = 0
    """Number of distinct image digests scanned."""
    tag_count: int = 0
    """Number of published tag references found in the publish configuration."""
    critical_total: int = 0
    """Total number of reported vulnerabilities across the distinct scanned digests."""
    scan_errors: int = 0
    """Number of images that could not be scanned or resolved."""


_SEVERITY_TRIVY: dict[str, str] = {
    "low": "LOW",
    "medium": "MEDIUM",
    "high": "HIGH",
    "critical": "CRITICAL",
}


def extract_images(data: Any) -> set[str]:
    """
    Recursively collect the container images referenced in a rendered Kubernetes manifest.

    Handles both the ``image: repository:tag`` form and the ``image: {repository:, tag:}``
    / ``image: {repository:, digest:}`` object forms used by the different charts.
    """
    images: set[str] = set()
    if isinstance(data, dict):
        image = data.get("image")
        if isinstance(image, str):
            images.add(image)
        elif isinstance(image, dict):
            repository = image.get("repository")
            if isinstance(repository, str):
                digest = image.get("digest")
                tag = image.get("tag")
                if isinstance(digest, str) and digest:
                    images.add(f"{repository}@{digest}")
                elif isinstance(tag, str) and tag:
                    images.add(f"{repository}:{tag}")
                else:
                    images.add(repository)
        for value in data.values():
            images |= extract_images(value)
    elif isinstance(data, (list, tuple, set)):
        for item in data:
            images |= extract_images(item)
    return images


async def discover_applications(
    cwd: anyio.Path,
    apps_root: str,
    environments: list[str],
    structure: str,
    excluded_apps: list[str],
) -> list[str]:
    """
    List the ArgoCD application directories of the repository that contain a ``Chart.yaml``.

    The applications are located under ``<apps_root>/<environment>/`` (the scanned environments
    are limited to ``environments``), as ``<app>/`` (``single``) or ``<client>/<app>/``
    (``two-level``). With ``structure='auto'`` both layouts are tried and merged. Vendored
    sub-charts (under a ``charts/`` directory) are never at these depths, so they are ignored.
    The returned paths are relative to ``cwd`` and sorted.
    """
    excluded_patterns = [re.compile(pattern) for pattern in excluded_apps]
    structures = ["single", "two-level"] if structure == "auto" else [structure]

    root = cwd / apps_root
    if not await root.exists():
        return []

    applications: set[str] = set()
    for environment in environments:
        environment_dir = root / environment
        if not await environment_dir.exists():
            continue
        for current_structure in structures:
            # single: <app>/Chart.yaml ; two-level: <client>/<app>/Chart.yaml
            sub_directories = 1 if current_structure == "single" else 2
            pattern = "/".join(["*"] * sub_directories) + "/Chart.yaml"
            async for chart_path in environment_dir.glob(pattern):
                relative_str = f"{apps_root}/{chart_path.parent.relative_to(root)}"
                if any(excluded.search(relative_str) for excluded in excluded_patterns):
                    _LOGGER.debug("Skipping excluded application %s", relative_str)
                    continue
                applications.add(relative_str)
    return sorted(applications)


async def application_images(
    cwd: anyio.Path,
    application: str,
) -> tuple[set[str], str]:
    """
    Render a single Helm application and extract the container images it references.

    Runs ``helm dependency update`` then ``helm template`` (best effort) on the application
    directory and collects the ``image`` values of the generated manifests.

    Returns the set of images and an error message (empty when the render succeeded).
    """
    app_dir = cwd / application
    env = {**os.environ, "HELM_CACHE_HOME": _TRIVY_CACHE_DIR.replace("trivy", "helm")}

    # No `--skip-refresh`: the dependency repositories of the ArgoCD applications are not added
    # with `helm repo add`, and Helm then fails to resolve them with that flag (it looks for an
    # index cache of the ad hoc `helm-manager-<hash>` repository that is never written), see
    # https://github.com/helm/helm/issues/11001 and https://github.com/helm/helm/issues/12921.
    await module_utils.run_timeout(
        ["helm", "dependency", "update"],
        env,
        _TIMEOUT_HELM_TEMPLATE,
        f"Helm dependency update {application}",
        f"Helm dependency update failed {application}",
        f"Helm dependency update timeout {application}",
        app_dir,
        error=False,
    )

    command = [
        "helm",
        "template",
        app_dir.name,
        ".",
        "--kube-version=1.25.0",
        "--namespace=default",
    ]
    stdout, success, _ = await module_utils.run_timeout(
        command,
        env,
        _TIMEOUT_HELM_TEMPLATE,
        f"Helm template {application}",
        f"Helm template failed {application}",
        f"Helm template timeout {application}",
        app_dir,
        error=False,
    )
    if not success or stdout is None:
        return set(), f"Failed to render the Helm chart of `{application}`"

    images: set[str] = set()
    try:
        for document in yaml.load_all(stdout, Loader=yaml.SafeLoader):
            images |= extract_images(document)
    except yaml.YAMLError as error:
        _LOGGER.warning("Failed to parse the rendered manifests of %s: %s", application, error)
        return images, f"Failed to parse the rendered manifests of `{application}`"
    return {image for image in images if "/" in image or ":" in image}, ""


def _parse_trivy_report(stdout: str) -> list[TrivyVulnerability]:
    """Parse the vulnerabilities of a ``trivy image --format json`` output."""
    vulnerabilities: list[TrivyVulnerability] = []
    try:
        report = json.loads(stdout)
    except json.JSONDecodeError:
        return vulnerabilities
    results = report.get("Results")
    if not isinstance(results, list):
        return vulnerabilities
    for result in results:
        if not isinstance(result, dict):
            continue
        found = result.get("Vulnerabilities")
        if not isinstance(found, list):
            continue
        for vulnerability in found:
            if not isinstance(vulnerability, dict):
                continue
            cve = str(vulnerability.get("VulnerabilityID", ""))
            references = [
                str(reference)
                for reference in vulnerability.get("References", [])
                if isinstance(reference, str)
            ]
            vulnerabilities.append(
                TrivyVulnerability(
                    cve=cve,
                    package=str(vulnerability.get("PkgName", "")),
                    installed_version=str(vulnerability.get("InstalledVersion", "")),
                    fixed_version=str(vulnerability.get("FixedVersion", "") or ""),
                    severity=str(vulnerability.get("Severity", "critical")).lower(),
                    title=str(vulnerability.get("Title", "")),
                    references=references,
                ),
            )
    return vulnerabilities


def _sbom_identity_regexp(image: str) -> str | None:
    """
    Return the cosign certificate identity regexp of an image.

    ``None`` is returned when the image registry cannot be mapped to a GitHub
    organization. The SBOM attestations of our images are signed keylessly by the
    publishing GitHub Actions workflow of their own repository; restricting the
    certificate identity to the organization that owns the image namespace prevents a
    forged attestation published from another account from being trusted.
    """
    if not image.startswith(f"{_GHCR_HOST}/"):
        return None
    organization = image[len(_GHCR_HOST) + 1 :].split("/", 1)[0]
    if not organization:
        return None
    return f"^https://github.com/{re.escape(organization)}/"


async def _fetch_sbom(image: str, env: dict[str, str], cwd: anyio.Path) -> str | None:
    """
    Fetch the CycloneDX SBOM attestation of an image, verified by cosign.

    Returns the SBOM document as a JSON string, or ``None`` when the image has no
    attestation, when the signature does not verify, or when cosign failed: the caller
    then falls back to scanning the whole image.
    """
    identity_regexp = _sbom_identity_regexp(image)
    if identity_regexp is None:
        return None
    command = [
        "cosign",
        "verify-attestation",
        image,
        "--type=cyclonedx",
        f"--certificate-oidc-issuer={_COSIGN_OIDC_ISSUER}",
        f"--certificate-identity-regexp={identity_regexp}",
    ]
    stdout, success, _ = await module_utils.run_timeout(
        command,
        env,
        _TIMEOUT_COSIGN,
        f"Cosign attestation {image}",
        f"Cosign attestation failed {image}",
        f"Cosign attestation timeout {image}",
        cwd,
        error=False,
    )
    if not success or stdout is None:
        return None
    try:
        attestations = json.loads(stdout)
    except json.JSONDecodeError:
        _LOGGER.warning("Could not parse the cosign output for %s", image)
        return None
    if not isinstance(attestations, list):
        return None
    for attestation in attestations:
        if not isinstance(attestation, dict):
            continue
        payload = attestation.get("payload")
        if not isinstance(payload, str):
            continue
        try:
            # The binascii.Error raised on an invalid base64 payload is a ValueError.
            statement = json.loads(base64.b64decode(payload))
        except ValueError:
            continue
        if not isinstance(statement, dict):
            continue
        predicate = statement.get("predicate")
        if isinstance(predicate, dict) and predicate.get("components") is not None:
            return json.dumps(predicate)
    return None


async def scan_image(
    image: str,
    severity: str,
    ignore_unfixed: bool,
    env: dict[str, str],
    cwd: anyio.Path,
) -> TrivyImage:
    """
    Scan a single container image with Trivy and return the reported vulnerabilities.

    When the image has a SBOM attestation signed by the GitHub organization owning its
    namespace, the (small) attestation is downloaded and scanned with ``trivy sbom``
    instead of pulling the whole image with ``trivy image``.
    """
    arguments = [
        "--format=json",
        f"--severity={_SEVERITY_TRIVY.get(severity, 'CRITICAL')}",
        "--exit-code=0",
    ]
    if ignore_unfixed:
        arguments.append("--ignore-unfixed")

    sbom = await _fetch_sbom(image, env, cwd)
    via_sbom = sbom is not None
    folder: str | None = None
    if sbom is None:
        command = ["trivy", "image", *arguments, image]
    else:
        folder = tempfile.mkdtemp(prefix="ghci-sbom-")
        filename = anyio.Path(folder) / "sbom.cdx.json"
        async with await filename.open("w", encoding="utf-8") as sbom_file:
            await sbom_file.write(sbom)
        command = ["trivy", "sbom", *arguments, str(filename)]

    try:
        stdout, success, _ = await module_utils.run_timeout(
            command,
            env,
            _TIMEOUT_TRIVY_IMAGE,
            f"Trivy scan {image}",
            f"Trivy scan failed {image}",
            f"Trivy scan timeout {image}",
            cwd,
            error=False,
        )
    finally:
        if folder is not None:
            # The temporary directory only holds the generated sbom.cdx.json.
            await (anyio.Path(folder) / "sbom.cdx.json").unlink(missing_ok=True)
            await anyio.Path(folder).rmdir()

    if not success or stdout is None:
        return TrivyImage(image=image, scan_error=f"Failed to scan the image `{image}`")
    vulnerabilities = _parse_trivy_report(stdout)
    return TrivyImage(
        image=image,
        critical_count=len(vulnerabilities),
        vulnerabilities=vulnerabilities,
        via_sbom=via_sbom,
    )


async def trivy_images(
    branch: str,
    cwd: anyio.Path,
    config: configuration.TrivyImageConfiguration,
    local_config: configuration.TrivyImageConfiguration,
) -> tuple[TrivyReport, list[module_utils.Message]]:
    """
    Scan the container images used by the ArgoCD applications of the checked out branch.

    The images are discovered by rendering each application Helm chart, then each distinct
    image reference is scanned once by Trivy (an image shared by several applications is not
    scanned twice). The vulnerabilities are reported back grouped by application.

    Returns the report and the list of messages logged during the scan.
    """
    messages: list[module_utils.Message] = []
    apps_root = local_config.get(
        "apps-root",
        config.get("apps-root", configuration.APPLICATIONS_ROOT_DIRECTORY_DEFAULT),
    )
    environments = local_config.get(
        "environments",
        config.get("environments", configuration.ENVIRONMENTS_DEFAULT),
    )
    structure = local_config.get(
        "app-structure",
        config.get("app-structure", configuration.APPLICATION_DIRECTORY_STRUCTURE_DEFAULT),
    )
    severity = local_config.get(
        "severity-threshold",
        config.get("severity-threshold", configuration.SEVERITY_THRESHOLD_DEFAULT),
    )
    ignore_unfixed = local_config.get(
        "ignore-unfixed",
        config.get("ignore-unfixed", configuration.IGNORE_UNFIXED_DEFAULT),
    )
    excluded_apps = local_config.get(
        "excluded-apps",
        config.get("excluded-apps", configuration.EXCLUDED_APPLICATIONS_DEFAULT),
    )

    await anyio.Path(_TRIVY_CACHE_DIR).mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "TRIVY_CACHE_DIR": _TRIVY_CACHE_DIR,
        "HELM_CACHE_HOME": "/var/www/.cache/helm",
    }

    applications = await discover_applications(cwd, apps_root, environments, structure, excluded_apps)
    if not applications:
        return (
            TrivyReport(branch=branch, applications=[], image_count=0),
            messages,
        )

    # Map every application to the images it references, and collect the distinct images.
    app_images: dict[str, set[str]] = {}
    app_errors: dict[str, str] = {}
    unique_images: set[str] = set()
    for application in applications:
        images, error = await application_images(cwd, application)
        app_images[application] = images
        if error:
            app_errors[application] = error
        unique_images |= images

    # Scan each distinct image exactly once.
    semaphore = asyncio.Semaphore(_TRIVY_CONCURRENCY)

    async def _scan(image: str) -> TrivyImage:
        async with semaphore:
            return await scan_image(image, severity, ignore_unfixed, env, cwd)

    scanned = await asyncio.gather(*(_scan(image) for image in sorted(unique_images)))
    scan_results = {result.image: result for result in scanned}

    applications_report: list[TrivyApplication] = []
    for application in applications:
        images_report: list[TrivyImage] = []
        for image in sorted(app_images[application]):
            result = scan_results.get(image)
            if result is not None:
                images_report.append(result)
            else:
                images_report.append(TrivyImage(image=image, scan_error=app_errors.get(application, "")))
        if app_errors.get(application) and not images_report:
            images_report.append(TrivyImage(image="", scan_error=app_errors[application]))
        applications_report.append(TrivyApplication(application=application, images=images_report))

    scan_errors = sum(1 for result in scanned if result.scan_error)
    critical_total = sum(result.critical_count for result in scanned)
    report = TrivyReport(
        branch=branch,
        applications=applications_report,
        image_count=len(unique_images),
        critical_total=critical_total,
        scan_errors=scan_errors,
    )
    return report, messages


# ---------------------------------------------------------------------------
# Trivy published container images scan
# ---------------------------------------------------------------------------

_SUPPORTED_REGISTRIES = ("ghcr.io", "docker.io")
"""The registries the published images scan supports (same restriction as the clean module)."""

_DOCKER_REGISTRY_API = "registry-1.docker.io"
"""The Docker Hub registry API host (``docker.io`` is a display alias of it)."""

_DOCKER_CONFIG_AUTH_KEY = "https://index.docker.io/v1/"
"""The auth key Trivy / go-containerregistry uses for Docker Hub credentials."""

_MANIFEST_ACCEPT = (
    "application/vnd.oci.image.index.v1+json,"
    "application/vnd.oci.image.manifest.v1+json,"
    "application/vnd.docker.distribution.manifest.v2+json,"
    "application/vnd.docker.distribution.manifest.list.v2+json"
)
"""The manifest media types accepted when resolving an image digest."""


class _PublishedRef(NamedTuple):
    """One published ``name:tag`` reference derived from the publish configuration."""

    host: str
    """The registry host (``ghcr.io`` or ``docker.io``)."""
    path: str
    """The image path in the registry (``owner/name`` on ghcr, ``name`` on Docker Hub)."""
    tag: str
    """The resolved tag name."""


def _docker_image_refs(docker_config: dict[str, Any], versions: list[str]) -> dict[_PublishedRef, set[str]]:
    """
    Build the published image references of a publish configuration for the given versions.

    Each image ``tags`` pattern is formatted with ``version=<version>`` for every supported
    version; patterns without a placeholder (the floating ``latest``, ``main``, ``test``, ...)
    collapse to a single reference shared by all the versions.

    Returns the references mapped to the versions that declare them.
    """
    refs: dict[_PublishedRef, set[str]] = {}
    repositories = docker_config.get("repository", tag_publish.configuration.DOCKER_REPOSITORY_DEFAULT)
    if not isinstance(repositories, dict) or not repositories:
        repositories = cast("dict[str, Any]", tag_publish.configuration.DOCKER_REPOSITORY_DEFAULT)
    for image in docker_config.get("images", []):
        if not isinstance(image, dict):
            continue
        name = image.get("name")
        if not isinstance(name, str):
            continue
        tags = image.get("tags", ["{version}"])
        if not isinstance(tags, list) or not tags:
            tags = ["{version}"]
        for repository_conf in repositories.values():
            if not isinstance(repository_conf, dict):
                continue
            host = repository_conf.get("host", "docker.io")
            if not isinstance(host, str) or host not in _SUPPORTED_REGISTRIES:
                _LOGGER.warning("Unsupported registry host %s for image %s", host, name)
                continue
            for tag_pattern in tags:
                for version in versions:
                    tag = str(tag_pattern).format(version=version)
                    refs.setdefault(_PublishedRef(host=host, path=name, tag=tag), set()).add(version)
    return refs


def _registry_api_host(host: str) -> str:
    """Return the registry API host used for the manifest requests."""
    return _DOCKER_REGISTRY_API if host == "docker.io" else host


async def _registry_pull_token(
    session: aiohttp.ClientSession,
    host: str,
    path: str,
    github_token: str,
) -> str | None:
    """Exchange the credentials for a short-lived registry pull token (``None`` for public Docker Hub)."""
    if host == "ghcr.io":
        auth: aiohttp.BasicAuth | None = aiohttp.BasicAuth("ghci", github_token)
        token_url = f"https://ghcr.io/token?scope=repository:{path}:pull"
    else:
        username = os.environ.get("DOCKERHUB_USERNAME")
        password = os.environ.get("DOCKERHUB_PASSWORD")
        auth = aiohttp.BasicAuth(username, password) if username and password else None
        token_url = f"https://auth.docker.io/token?service=registry.docker.io&scope=repository:{path}:pull"
    try:
        async with session.get(token_url, auth=auth) as response:
            if response.status != 200:
                return None
            data = await response.json(content_type=None)
    except (aiohttp.ClientError, TimeoutError, ValueError):
        # A missing token only degrades to "tag not resolvable", it is not a fatal error.
        return None
    token = data.get("token") if isinstance(data, dict) else None
    return str(token) if isinstance(token, str) else None


async def _resolve_tag_digest(
    session: aiohttp.ClientSession,
    host: str,
    path: str,
    tag: str,
    github_token: str,
) -> tuple[str | None, str]:
    """Resolve a ``name:tag`` reference to its manifest digest, best effort."""
    pull_token = await _registry_pull_token(session, host, path, github_token)
    headers = {"Accept": _MANIFEST_ACCEPT}
    if pull_token:
        headers["Authorization"] = f"Bearer {pull_token}"
    url = f"https://{_registry_api_host(host)}/v2/{path}/manifests/{tag}"
    try:
        async with session.head(url, headers=headers, allow_redirects=True) as response:
            if response.status == 404:
                return None, "not-published"
            if response.status != 200:
                return None, f"registry error {response.status}"
            digest = response.headers.get("Docker-Content-Digest", "")
    except (aiohttp.ClientError, TimeoutError) as error:
        return None, f"registry error: {error}"
    return (digest, "") if digest else (None, "no digest in the response")


async def _docker_config_dir(hosts: set[str], github_token: str) -> str:
    """
    Write a temporary Docker config file with the registry credentials Trivy will reuse.

    Returns the path of the directory to be set as ``DOCKER_CONFIG``.
    """
    auths: dict[str, dict[str, str]] = {}
    if any(host == "ghcr.io" for host in hosts):
        auths["ghcr.io"] = {"auth": base64.b64encode(f"ghci:{github_token}".encode()).decode()}
    if any(host == "docker.io" for host in hosts):
        username = os.environ.get("DOCKERHUB_USERNAME")
        password = os.environ.get("DOCKERHUB_PASSWORD")
        if username and password:
            entry = {"auth": base64.b64encode(f"{username}:{password}".encode()).decode()}
            auths[_DOCKER_CONFIG_AUTH_KEY] = entry
            auths["docker.io"] = entry
    config_dir = anyio.Path(tempfile.mkdtemp(prefix="ghci-trivy-"))
    async with await (config_dir / "config.json").open("w", encoding="utf-8") as file:
        await file.write(json.dumps({"auths": auths}))
    return str(config_dir)


async def trivy_published_images(
    branch: str,
    cwd: anyio.Path,
    config: configuration.TrivyImageConfiguration,
    local_config: configuration.TrivyImageConfiguration,
    versions: list[str],
    github_token: str,
) -> tuple[TrivyPublishedReport, list[module_utils.Message]]:
    """
    Scan the container images published by the repository with Trivy, deduplicated by digest.

    The references are derived from the ``.github/publish.yaml`` configuration of the checked
    out branch, for all the supported versions. Every floating tag is resolved to its manifest
    digest through the registry v2 API; the tags sharing a digest are grouped and scanned once,
    and the report lists the equivalent tags and versions of each scanned image.

    Returns the report and the list of messages logged during the scan.
    """
    messages: list[module_utils.Message] = []
    severity = local_config.get(
        "severity-threshold",
        config.get("severity-threshold", configuration.SEVERITY_THRESHOLD_DEFAULT),
    )
    ignore_unfixed = local_config.get(
        "ignore-unfixed",
        config.get("ignore-unfixed", configuration.IGNORE_UNFIXED_DEFAULT),
    )

    publish_path = cwd / ".github" / "publish.yaml"
    if not await publish_path.exists():
        return TrivyPublishedReport(branch=branch), messages
    async with await publish_path.open("r", encoding="utf-8") as file:
        publish_config = yaml.load(await file.read(), Loader=yaml.SafeLoader) or {}
    if not isinstance(publish_config, dict):
        return TrivyPublishedReport(branch=branch), messages
    docker_config = publish_config.get("docker", {})
    refs = _docker_image_refs(docker_config, versions)
    if not refs:
        return TrivyPublishedReport(branch=branch), messages

    await anyio.Path(_TRIVY_CACHE_DIR).mkdir(parents=True, exist_ok=True)
    timeout = aiohttp.ClientTimeout(total=_TIMEOUT_REGISTRY.total_seconds())

    # Resolve every tag to its digest and group the tags that share the same manifest.
    digests: dict[tuple[str, str, str], dict[str, Any]] = {}
    not_published: list[str] = []
    resolve_errors: list[str] = []
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for ref, ref_versions in sorted(refs.items()):
            display = f"{ref.host}/{ref.path}:{ref.tag}"
            digest, error = await _resolve_tag_digest(session, ref.host, ref.path, ref.tag, github_token)
            if digest is None:
                if error == "not-published":
                    not_published.append(display)
                else:
                    _LOGGER.warning("Could not resolve the digest of %s: %s", display, error)
                    resolve_errors.append(f"{display} ({error})")
                continue
            group = digests.setdefault((ref.host, ref.path, digest), {"tags": set(), "versions": set()})
            group["tags"].add(ref.tag)
            group["versions"] |= ref_versions

    hosts = {host for host, _path, _digest in digests}
    env = {
        **os.environ,
        "TRIVY_CACHE_DIR": _TRIVY_CACHE_DIR,
        "HELM_CACHE_HOME": "/var/www/.cache/helm",
    }
    config_dir = await _docker_config_dir(hosts, github_token)
    env["DOCKER_CONFIG"] = config_dir
    scanned: list[TrivyImage] = []
    try:
        ordered = sorted(digests.items())
        semaphore = asyncio.Semaphore(_TRIVY_CONCURRENCY)

        async def _scan(key: tuple[str, str, str]) -> TrivyImage:
            host, path, digest = key
            async with semaphore:
                return await scan_image(f"{host}/{path}@{digest}", severity, ignore_unfixed, env, cwd)

        scanned = await asyncio.gather(*(_scan(key) for key, _group in ordered))
    finally:
        # The temporary directory only holds the generated config.json.
        await (anyio.Path(config_dir) / "config.json").unlink(missing_ok=True)
        await anyio.Path(config_dir).rmdir()

    images: list[TrivyPublishedImage] = []
    for (key, group), result in zip(ordered, scanned, strict=True):
        host, path, digest = key
        images.append(
            TrivyPublishedImage(
                repository=f"{host}/{path}",
                digest=digest,
                tags=sorted(group["tags"]),
                versions=sorted(group["versions"]),
                critical_count=result.critical_count,
                vulnerabilities=result.vulnerabilities,
                scan_error=result.scan_error,
                via_sbom=result.via_sbom,
            )
        )

    if not_published:
        _LOGGER.info("Tags not published (skipped): %s", ", ".join(sorted(not_published)))
    if resolve_errors:
        messages.append(
            module_utils.HtmlMessage("\n".join(f"- `{item}`" for item in sorted(resolve_errors))),
        )
        messages[-1].title = "Published tags whose digest could not be resolved"

    scan_errors = sum(1 for result in scanned if result.scan_error)
    critical_total = sum(result.critical_count for result in scanned)
    report = TrivyPublishedReport(
        branch=branch,
        images=images,
        image_count=len(images),
        tag_count=len(refs),
        critical_total=critical_total,
        scan_errors=scan_errors,
    )
    return report, messages


# ---------------------------------------------------------------------------
# Trivy dependencies scan and fix (the Python, npm and Gradle replacements of the Snyk auto-fix)
# ---------------------------------------------------------------------------

_TRIVY_PYTHON_TYPES = {"pip", "poetry", "pipenv"}
"""The Trivy result types of the Python dependency files."""

_TRIVY_JAVA_TYPES = {"gradle"}
"""The Trivy result types of the Java dependency files (``gradle.lockfile``)."""

_TRIVY_DEPENDENCY_TYPES = _TRIVY_PYTHON_TYPES | {"npm"} | _TRIVY_JAVA_TYPES
"""The Trivy result types of the dependency files, the scope of the dependencies audit."""

_TRIVY_ALL_SEVERITIES = "LOW,MEDIUM,HIGH,CRITICAL,UNKNOWN"
"""Trivy reports UNKNOWN severities only when they are part of the requested severity list."""


class DependencyVulnerability(BaseModel):
    """A single dependency vulnerability reported by ``trivy fs``."""

    file: str
    """The dependency file the package comes from, relative to the repository root."""
    package_name: str
    """The vulnerable package name (a Maven ``group:artifact`` coordinate for Gradle)."""
    installed_version: str
    """The vulnerable version in the dependency file."""
    dependency_type: str
    """The Trivy dependency type (``pip``, ``poetry``, ``pipenv``, ``npm``, ``gradle``)."""
    severity: str
    """The vulnerability severity (lowercase)."""
    trivy_id: str
    """The Trivy vulnerability ID (CVE when known, otherwise the database ID)."""
    fixed_versions: list[str] = []
    """The versions that fix the vulnerability, empty when there is no fix yet."""
    cve_ids: list[str] = []
    """The CVE identifiers of the vulnerability."""
    cwe_ids: list[str] = []
    """The CWE identifiers of the vulnerability."""
    title: str = ""
    """A short description of the vulnerability."""
    id_url: str = ""
    """The URL of the vulnerability page in the Trivy vulnerability database."""


class DependencyFixPlan(BaseModel):
    """The planned fix of one vulnerable package in one dependency file."""

    file: str
    """The dependency file to fix, relative to the repository root."""
    package_name: str
    """The package to bump."""
    installed_version: str
    """The currently vulnerable version."""
    target_version: str
    """The smallest version fixing all the selected vulnerabilities of the package."""
    vulnerabilities: list[DependencyVulnerability] = []
    """The vulnerabilities the fix addresses."""


class TrivyFilesystemResult(NamedTuple):
    """The result of a Trivy dependencies audit run on a branch."""

    messages: list[module_utils.Message]
    """The output messages (scan and fix details)."""
    fix_message: module_utils.HtmlMessage | None
    """The message of the fix step, rendered in the pull request."""
    short_messages: list[str]
    """The dashboard's messages (with a resume of the vulnerabilities)."""
    success: bool
    """Is on success (scan errors only, skipped fixes are reported but not errors)."""
    vulnerabilities: dict[str, list[VulnerabilityData]]
    """The file-grouped remaining vulnerability data, after the fixes and the ignore policies."""
    fixed_vulnerabilities: dict[str, list[VulnerabilityData]]
    """The file-grouped vulnerabilities fixed by this run, confirmed by the rescan."""
    pinned_vulnerabilities: dict[str, list[PinnedVulnerability]]
    """The file-grouped vulnerabilities addressed by floor pins added to the requirements files."""
    skipped_fixes: list[str]
    """The human readable reasons of the fixes that were not applied."""


def parse_trivy_fs_report(stdout: str | None) -> list[DependencyVulnerability]:
    """Parse the dependency vulnerabilities of a ``trivy fs --format json`` output."""
    vulnerabilities: list[DependencyVulnerability] = []
    if not stdout:
        return vulnerabilities
    try:
        report = json.loads(stdout)
    except json.JSONDecodeError:
        _LOGGER.warning("Invalid Trivy JSON output")
        return vulnerabilities
    results = report.get("Results") if isinstance(report, dict) else None
    if not isinstance(results, list):
        return vulnerabilities
    for result in results:
        if not isinstance(result, dict) or result.get("Type") not in _TRIVY_DEPENDENCY_TYPES:
            continue
        target = _normalize_file_name(str(result.get("Target", "")))
        if not target:
            continue
        found = result.get("Vulnerabilities")
        if not isinstance(found, list):
            continue
        for vulnerability in found:
            if not isinstance(vulnerability, dict):
                continue
            trivy_id = str(vulnerability.get("VulnerabilityID", ""))
            aliases = [str(alias) for alias in vulnerability.get("Aliases", []) if isinstance(alias, str)]
            cve_ids = sorted({alias for alias in aliases if alias.startswith("CVE-")})
            if trivy_id.startswith("CVE-") and trivy_id not in cve_ids:
                cve_ids.append(trivy_id)
            fixed_versions = [
                version.strip()
                for version in str(vulnerability.get("FixedVersion", "") or "").split(",")
                if version.strip()
            ]
            severity = str(vulnerability.get("Severity", "unknown")).lower()
            vulnerabilities.append(
                DependencyVulnerability(
                    file=target,
                    package_name=str(vulnerability.get("PkgName", "")),
                    installed_version=str(vulnerability.get("InstalledVersion", "")),
                    dependency_type=str(result.get("Type", "")),
                    severity=severity,
                    trivy_id=trivy_id,
                    fixed_versions=fixed_versions,
                    cve_ids=cve_ids,
                    cwe_ids=[str(cwe) for cwe in vulnerability.get("CweIDs", []) if isinstance(cwe, str)],
                    title=str(vulnerability.get("Title", "")),
                    id_url=str(vulnerability.get("PrimaryURL", "")),
                ),
            )
    return vulnerabilities


def _dependency_title(vulnerability: DependencyVulnerability) -> str:
    """Build the dashboard title of a Trivy dependency vulnerability, like the Snyk one."""
    title = (
        f"[{vulnerability.severity.upper()}] "
        f"{vulnerability.package_name}@{vulnerability.installed_version}: "
        f"{vulnerability.trivy_id}"
    )
    if vulnerability.cwe_ids:
        title += " " + " ".join(vulnerability.cwe_ids)
    if vulnerability.fixed_versions:
        title += " [Fixed in: " + ", ".join(vulnerability.fixed_versions) + "]"
    else:
        title += " [No fix yet]"
    return title + "."


def dependency_to_vulnerability_data(vulnerability: DependencyVulnerability) -> VulnerabilityData:
    """Convert a Trivy dependency vulnerability to the generic report structure."""
    return VulnerabilityData(
        file=vulnerability.file,
        package_name=vulnerability.package_name,
        package_version=vulnerability.installed_version,
        package_manager=vulnerability.dependency_type,
        severity=vulnerability.severity,
        snyk_id=vulnerability.trivy_id,
        cve_ids=vulnerability.cve_ids,
        cwe_ids=vulnerability.cwe_ids,
        title=_dependency_title(vulnerability),
        fixed_in=vulnerability.fixed_versions,
        is_upgradable=bool(vulnerability.fixed_versions),
        is_patchable=False,
        id_url=vulnerability.id_url,
    )


def dependency_file_vulnerabilities(
    vulnerabilities: list[DependencyVulnerability],
) -> dict[str, list[VulnerabilityData]]:
    """Group the Trivy dependency vulnerabilities by file, as the Snyk report structure."""
    grouped: dict[str, list[VulnerabilityData]] = {}
    for vulnerability in sorted(vulnerabilities, key=_vulnerability_sort_key_dependency):
        grouped.setdefault(vulnerability.file, []).append(
            dependency_to_vulnerability_data(vulnerability),
        )
    return dict(sorted(grouped.items()))


def _vulnerability_sort_key_dependency(vulnerability: DependencyVulnerability) -> tuple[int, str, str, str]:
    """Sort the vulnerabilities by descending severity, then by file, package and ID."""
    return (
        -SEVERITY_ORDER.get(vulnerability.severity, 0),
        vulnerability.file,
        vulnerability.package_name,
        vulnerability.trivy_id,
    )


def choose_target_version(vulnerabilities: list[DependencyVulnerability]) -> str | None:
    """
    Get the smallest version that fixes all the given vulnerabilities of one package.

    Each vulnerability lists the fixed versions of its release lines; the smallest candidate
    above the installed version is selected per vulnerability, and the greatest of them becomes
    the target. No candidate above the installed version means the package cannot be fixed by a
    bump, None is returned.
    """
    target: packaging.version.Version | None = None
    for vulnerability in vulnerabilities:
        try:
            installed = packaging.version.Version(vulnerability.installed_version)
            candidates = sorted(
                candidate
                for candidate in (
                    packaging.version.Version(version) for version in vulnerability.fixed_versions
                )
                if candidate > installed
            )
        except packaging.version.InvalidVersion:
            return None
        if not candidates:
            return None
        target = candidates[0] if target is None else max(target, candidates[0])
    return str(target) if target is not None else None


_MAVEN_QUALIFIER_RANK = {
    "alpha": 1,
    "a": 1,
    "beta": 2,
    "b": 2,
    "milestone": 3,
    "m": 3,
    "rc": 4,
    "cr": 4,
    "snapshot": 5,
    "": 6,
    "final": 6,
    "ga": 6,
    "release": 6,
    "sp": 7,
    "split": 7,
}
"""
Ranks of the documented Maven version qualifiers (lowercase).

A numeric core and the empty/``final``/``ga``/``release`` qualifiers share the rank ``6``;
unknown qualifiers get a higher rank. This is a pragmatic ordering that covers the dotted
numeric versions Trivy reports for Java, not the full Maven ``ComparableVersion`` algorithm;
the versions it cannot model safely are skipped rather than mis-ordered.
"""


def _maven_sort_key(version: str) -> tuple[list[int], int, str] | None:
    """Build a comparable key of a Maven version, None when the version is too complex to order."""
    v = version.strip()
    if not v or any(char in v for char in "(),[]@*?+= "):
        return None
    match = re.match(r"^(\d+(?:\.\d+)*)(?:[._-]([A-Za-z0-9]+))?$", v)
    if match is None:
        return None
    core = [int(part) for part in match.group(1).split(".")]
    qualifier = (match.group(2) or "").lower()
    return core, _MAVEN_QUALIFIER_RANK.get(qualifier, 8), qualifier


def choose_java_target_version(vulnerabilities: list[DependencyVulnerability]) -> str | None:
    """
    Get the smallest Maven version that fixes all the given vulnerabilities of one package.

    Mirrors :func:`choose_target_version` for the Gradle ecosystem, using a Maven-aware
    comparison. Returns None when the installed or a candidate version cannot be ordered
    safely, or when no candidate is above the installed version.
    """
    target_key: tuple[list[int], int, str] | None = None
    target_version: str | None = None
    for vulnerability in vulnerabilities:
        installed_key = _maven_sort_key(vulnerability.installed_version)
        if installed_key is None:
            return None
        best_key: tuple[list[int], int, str] | None = None
        best_version: str | None = None
        for version in vulnerability.fixed_versions:
            key = _maven_sort_key(version)
            if key is None or key <= installed_key:
                continue
            if best_key is None or key < best_key:
                best_key, best_version = key, version
        if best_key is None:
            return None
        if target_key is None or best_key > target_key:
            target_key, target_version = best_key, best_version
    return target_version


def fixed_dependency_vulnerabilities(
    before: dict[str, list[VulnerabilityData]],
    after: dict[str, list[VulnerabilityData]],
) -> dict[str, list[VulnerabilityData]]:
    """
    Get the Trivy vulnerabilities fixed by the fixes, confirmed by the rescan.

    A vulnerability is fixed when the rescan does not report it anymore for the file and
    package, on any version: a vulnerability still reported on the bumped version is not a fix.
    """
    after_keys = {
        (file_name, vulnerability.package_name, vulnerability.snyk_id)
        for file_name, vulnerabilities in after.items()
        for vulnerability in vulnerabilities
    }
    fixed: dict[str, list[VulnerabilityData]] = {}
    for file_name, vulnerabilities in before.items():
        for vulnerability in vulnerabilities:
            if (file_name, vulnerability.package_name, vulnerability.snyk_id) in after_keys:
                continue
            fixed.setdefault(file_name, []).append(vulnerability)
    for vulnerabilities in fixed.values():
        vulnerabilities.sort(key=_vulnerability_sort_key)
    return dict(sorted(fixed.items()))


async def _trivy_fs_scan(
    env: dict[str, str],
    cwd: anyio.Path,
    *,
    ignore_policies: bool,
    message_title: str,
    severity: str = _TRIVY_ALL_SEVERITIES,
) -> tuple[list[DependencyVulnerability], bool]:
    """
    Scan the repository dependency files with ``trivy fs``, filtering the policies when asked.

    With ``ignore_policies`` the ``.trivyignore`` files are bypassed (``--ignorefile=/dev/null``)
    to get the complete list, including the ignored vulnerabilities.
    """
    await anyio.Path(_TRIVY_CACHE_DIR).mkdir(parents=True, exist_ok=True)
    command = [
        "trivy",
        "fs",
        "--scanners=vuln",
        "--format=json",
        f"--severity={severity}",
        "--exit-code=0",
        *(["--ignorefile=/dev/null"] if ignore_policies else []),
        ".",
    ]
    run_env = {**env, "TRIVY_CACHE_DIR": _TRIVY_CACHE_DIR}
    stdout, success, message = await module_utils.run_timeout(
        command,
        run_env,
        _TIMEOUT_TRIVY_FS,
        message_title,
        f"Error while running {message_title}",
        f"Timeout while running {message_title}",
        cwd,
        error=False,
    )
    if not success:
        if message is not None:
            _LOGGER.warning(message)
        return [], False
    return parse_trivy_fs_report(stdout), True


def _is_fixable_file(vulnerability: DependencyVulnerability) -> bool:
    """Tell whether the dependency file of a vulnerability is in the scope of the fix engine."""
    if vulnerability.dependency_type in _TRIVY_JAVA_TYPES:
        return vulnerability.file.endswith("gradle.lockfile")
    if vulnerability.dependency_type == "poetry":
        return vulnerability.file.endswith("poetry.lock")
    if vulnerability.dependency_type == "pip":
        return vulnerability.file.endswith(".txt")
    if vulnerability.dependency_type == "npm":
        return vulnerability.file.endswith("package-lock.json")
    return False


def build_dependency_fix_plans(
    vulnerabilities: list[DependencyVulnerability],
    fix_threshold: str,
    files_no_fix: list[str],
) -> tuple[list[DependencyFixPlan], list[str]]:
    """
    Build the fix plan of the vulnerable packages, and the reasons of the skipped fixes.

    A plan bumps one package of one dependency file to the smallest version fixing all the
    vulnerabilities of that package at or above ``fix_threshold``. The vulnerabilities below
    the threshold, in the ``files_no_fix`` files, in unsupported files, or without a usable
    fixed version are skipped with a reason.
    """
    min_fix_severity = SEVERITY_ORDER.get(fix_threshold, 2)
    grouped: dict[tuple[str, str], list[DependencyVulnerability]] = {}
    for vulnerability in vulnerabilities:
        grouped.setdefault((vulnerability.file, vulnerability.package_name), []).append(vulnerability)

    plans: list[DependencyFixPlan] = []
    skipped: list[str] = []
    for (file_name, package_name), file_vulnerabilities in sorted(grouped.items()):
        fixable = [
            vulnerability
            for vulnerability in file_vulnerabilities
            if SEVERITY_ORDER.get(vulnerability.severity, 0) >= min_fix_severity
        ]
        if not fixable:
            continue
        if file_name in files_no_fix:
            skipped.append(
                f"{package_name} in `{file_name}`: fixes are disabled for this file (`files-no-fix`)"
            )
            continue
        if not _is_fixable_file(fixable[0]):
            skipped.append(f"{package_name} in `{file_name}`: not auto-fixable, unsupported file type")
            continue
        if fixable[0].dependency_type in _TRIVY_JAVA_TYPES:
            target = choose_java_target_version(fixable)
        else:
            target = choose_target_version(fixable)
        if target is None:
            skipped.append(
                f"{fixable[0].package_name} {fixable[0].installed_version} in `{file_name}`:"
                " no fixed version available"
            )
            continue
        plans.append(
            DependencyFixPlan(
                file=file_name,
                package_name=package_name,
                installed_version=fixable[0].installed_version,
                target_version=target,
                vulnerabilities=fixable,
            ),
        )
    return plans, skipped


async def filter_python_incompatible_plans(
    plans: list[DependencyFixPlan],
    python_version: str,
) -> tuple[list[DependencyFixPlan], list[str]]:
    """
    Split the plans between those installable on the branch Python and those that are not.

    Only the Python plans are checked against PyPI `Requires-Python`; the npm plans are always
    kept, as the branch Python version does not constrain them.
    """
    if not python_version:
        return plans, []
    python_plans = [plan for plan in plans if not plan.file.endswith("package-lock.json")]
    npm_plans = [plan for plan in plans if plan.file.endswith("package-lock.json")]
    unique = sorted({(plan.package_name, plan.target_version) for plan in python_plans})
    requires_python: dict[tuple[str, str], str | None] = {}
    timeout = aiohttp.ClientTimeout(total=settings.audit.timeouts.snyk_api.total_seconds())
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with asyncio.TaskGroup() as group:
            fetch_tasks = {
                key: group.create_task(_fetch_requires_python(session, key[0], key[1])) for key in unique
            }
        for key, fetch_task in fetch_tasks.items():
            requires_python[key] = fetch_task.result()

    kept: list[DependencyFixPlan] = list(npm_plans)
    skipped: list[str] = []
    for plan in python_plans:
        spec = requires_python.get((plan.package_name, plan.target_version))
        if is_python_incompatible(spec, python_version):
            skipped.append(
                f"{plan.package_name} {plan.installed_version} -> {plan.target_version}:"
                f" requires a newer Python than the branch Python {python_version}"
            )
        else:
            kept.append(plan)
    return kept, skipped


_PYPROJECT_CLASSIC_ENTRY_RE = r'^(?P<prefix>\s*{name}\s*=\s*")(?P<spec>[^"]*)(?P<suffix>"\s*(?:#.*)?)$'
"""A `[tool.poetry.dependencies]` style entry line of a pyproject.toml file."""

_PYPROJECT_PEP621_ENTRY_RE = (
    r'^(?P<prefix>\s*")(?P<package>{name}(?:\s*\[[^\]]*\])?\s*)'
    r'(?P<spec>(?:==|>=|~=|<=|<|>|!=)[^"]*)'
    r'(?P<suffix>"\s*,?\s*(?:#.*)?)$'
)
"""A `"name>=version"` style entry line of a PEP 621 `dependencies` array."""

_POETRY_DEPS_SECTION_RE = re.compile(
    r"^\[tool\.poetry\.dependencies\]$",
    re.MULTILINE,
)
"""The classical Poetry main dependencies section header."""


def _poetry_entry_regex(name: str, entry_regex: str) -> re.Pattern[str]:
    """Build the entry line pattern of a package name, tolerant to case."""
    return re.compile(
        entry_regex.format(name="".join(f"[{char.upper()}{char.lower()}]" for char in name)),
        re.MULTILINE,
    )


def find_pyproject_constraint(pyproject_content: str, package_name: str) -> str | None:
    """
    Get the declared PEP 508 requirement of a package in a pyproject.toml content.

    Supports the `[tool.poetry.dependencies]` table entries and the PEP 621
    `dependencies = [...]` string entries. None when the package is not a direct dependency.
    """
    match = _poetry_entry_regex(package_name, _PYPROJECT_CLASSIC_ENTRY_RE).search(pyproject_content)
    if match is not None:
        return f"{package_name} {match['spec']}"
    match = _poetry_entry_regex(package_name, _PYPROJECT_PEP621_ENTRY_RE).search(pyproject_content)
    if match is not None:
        return f"{match['package'].strip()} {match['spec']}"
    return None


def apply_temp_poetry_constraint(pyproject_content: str, package_name: str, version: str) -> str | None:
    """
    Return the pyproject content with a temporary `==version` constraint for the package.

    An existing direct constraint is replaced, otherwise a dependency entry is injected in the
    `[tool.poetry.dependencies]` section or in the PEP 621 `dependencies` array, to force the
    conservative `poetry lock` to move a transitive dependency. None when the file format is
    not supported.
    """
    for entry_regex in (_PYPROJECT_CLASSIC_ENTRY_RE, _PYPROJECT_PEP621_ENTRY_RE):
        pattern = _poetry_entry_regex(package_name, entry_regex)
        match = pattern.search(pyproject_content)
        if match is not None:
            start, end = match.span("spec")
            return pyproject_content[:start] + f"=={version}" + pyproject_content[end:]

    section_match = _POETRY_DEPS_SECTION_RE.search(pyproject_content)
    if section_match is not None:
        end = pyproject_content.find("\n[", section_match.end())
        insert_at = len(pyproject_content) if end == -1 else end
        return (
            pyproject_content[:insert_at].rstrip("\n")
            + f'\n{package_name} = "=={version}"\n'
            + pyproject_content[insert_at:]
        )
    pep621_match = re.search(r"^dependencies\s*=\s*\[", pyproject_content, re.MULTILINE)
    if pep621_match is not None:
        end = pyproject_content.find("]", pep621_match.end())
        if end != -1:
            return (
                pyproject_content[:end].rstrip()
                + f'\n    "{package_name}=={version}",\n'
                + pyproject_content[end:]
            )
    return None


def locked_poetry_version(lock_content: str, package_name: str) -> str | None:
    """Get the locked version of a package in a poetry.lock content, None when it is not locked."""
    pattern = re.compile(
        r'^name = "'
        + "".join(f"[{char.upper()}{char.lower()}]" for char in package_name)
        + r'"[ \t]*\nversion = "([^"]+)"',
        re.MULTILINE,
    )
    match = pattern.search(lock_content)
    return match.group(1) if match is not None else None


async def _fix_poetry_project(
    cwd: anyio.Path,
    directory: str,
    plans: list[DependencyFixPlan],
    env: dict[str, str],
) -> tuple[list[str], list[str], list[str]]:
    """
    Bump the locked versions of the planned packages, leaving the pyproject.toml untouched.

    For each package: a temporary `==target` constraint is written in the pyproject.toml (an
    existing constraint or an injected transitive dependency), `poetry lock` moves only that
    entry, the original pyproject.toml is restored and `poetry lock` is run again to keep the
    bumped version with the original constraints, validated by `poetry check --lock`.
    """
    applied: list[str] = []
    skipped: list[str] = []
    logs: list[str] = []
    project_dir = cwd / directory if directory else cwd
    pyproject_path = project_dir / "pyproject.toml"
    lock_path = project_dir / "poetry.lock"
    if not await pyproject_path.exists():
        return (
            applied,
            [
                f"{plan.package_name} in `{plan.file}`: no pyproject.toml next to the lock file"
                for plan in plans
            ],
            logs,
        )
    original_pyproject = await pyproject_path.read_text(encoding="utf-8")
    original_lock = await lock_path.read_text(encoding="utf-8")

    for plan in plans:
        constraint = find_pyproject_constraint(original_pyproject, plan.package_name)
        if constraint is not None:
            try:
                allowed = packaging.requirements.Requirement(constraint).specifier
            except packaging.requirements.InvalidRequirement:
                allowed = None
            if allowed is not None and not allowed.contains(
                packaging.version.Version(plan.target_version),
                prereleases=True,
            ):
                skipped.append(
                    f"{plan.package_name} {plan.installed_version} -> {plan.target_version}:"
                    f" the pyproject.toml constraint `{constraint}` does not allow it,"
                    " handled by the dependency updates"
                )
                continue

        temp_pyproject = apply_temp_poetry_constraint(
            original_pyproject,
            plan.package_name,
            plan.target_version,
        )
        if temp_pyproject is None:
            skipped.append(
                f"{plan.package_name} in `{plan.file}`: unsupported pyproject.toml format for the fix"
            )
            continue

        await pyproject_path.write_text(temp_pyproject, encoding="utf-8")
        _, lock_success, message = await module_utils.run_timeout(
            ["poetry", "lock"],
            env,
            _TIMEOUT_POETRY_LOCK,
            f"Poetry lock {directory} {plan.package_name}=={plan.target_version}",
            f"Poetry lock failed {directory} {plan.package_name}",
            f"Poetry lock timeout {directory} {plan.package_name}",
            project_dir,
            error=False,
        )
        if message is not None:
            logs.append(message.to_plain_text())
        if not lock_success:
            await pyproject_path.write_text(original_pyproject, encoding="utf-8")
            await lock_path.write_text(original_lock, encoding="utf-8")
            skipped.append(
                f"{plan.package_name} {plan.installed_version} -> {plan.target_version}:"
                " version solving failed, the fixed version is not installable"
                " with the other constraints"
            )
            continue

        await pyproject_path.write_text(original_pyproject, encoding="utf-8")
        _, lock_success, message = await module_utils.run_timeout(
            ["poetry", "lock"],
            env,
            _TIMEOUT_POETRY_LOCK,
            f"Poetry lock restore {directory} {plan.package_name}",
            f"Poetry lock restore failed {directory} {plan.package_name}",
            f"Poetry lock restore timeout {directory} {plan.package_name}",
            project_dir,
            error=False,
        )
        if message is not None:
            logs.append(message.to_plain_text())
        if not lock_success:
            await pyproject_path.write_text(original_pyproject, encoding="utf-8")
            await lock_path.write_text(original_lock, encoding="utf-8")
            skipped.append(
                f"{plan.package_name} in `{plan.file}`: the lock file could not be regenerated"
                " with the original constraints, reverted"
            )
            continue

        _, check_success, message = await module_utils.run_timeout(
            ["poetry", "check", "--lock"],
            env,
            _TIMEOUT_POETRY_CHECK_LOCK,
            f"Poetry check {directory}",
            f"Poetry check failed {directory}",
            f"Poetry check timeout {directory}",
            project_dir,
            error=False,
        )
        if not check_success:
            await pyproject_path.write_text(original_pyproject, encoding="utf-8")
            await lock_path.write_text(original_lock, encoding="utf-8")
            skipped.append(
                f"{plan.package_name} in `{plan.file}`: the regenerated lock file is not consistent"
                " with the pyproject.toml, reverted"
            )
            continue

        new_lock = await lock_path.read_text(encoding="utf-8")
        locked = locked_poetry_version(new_lock, plan.package_name)
        if locked != plan.target_version:
            await pyproject_path.write_text(original_pyproject, encoding="utf-8")
            await lock_path.write_text(original_lock, encoding="utf-8")
            skipped.append(
                f"{plan.package_name} in `{plan.file}`: the lock kept the version `{locked}`"
                f" instead of `{plan.target_version}`, reverted"
            )
            continue
        applied.append(f"{plan.package_name} {plan.installed_version} -> {locked} in `{plan.file}`")

    return applied, skipped, logs


_REQUIREMENT_LINE_RE_EXTENDED = re.compile(
    r"^(?P<pre>\s*)(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)"
    r"(?P<extras>\s*\[[^\]]*\])?"
    r"(?P<gap1>\s*)(?P<first_op>==|>=|~=|<=|<|>|!=)(?P<first_version>[^#;\s]*)"
    r"(?P<rest>[^#;]*)"
    r"(?P<post>.*)$",
)
"""A requirement line with a version constraint, keeping the parts around the first version."""


def bump_requirement_content(
    content: str,
    package_name: str,
    target_version: str,
) -> tuple[str, str]:
    """
    Bump or append the requirement of a package in a requirements file content.

    Returns the new content and an outcome among `bumped`, `appended`, `already` or a skip
    reason prefixed by `skip:`. A direct `==`/`>=` constraint is bumped in place, a package
    that is not listed gets a `>=` floor line appended, bounded or unpinned requirements
    are skipped.
    """
    lines = content.splitlines(keepends=True)
    for index, line in enumerate(lines):
        match = _REQUIREMENT_LINE_RE_EXTENDED.match(line.rstrip("\n"))
        if match is None or _normalize_package_name(match["name"]) != _normalize_package_name(package_name):
            continue
        if match["first_op"] not in ("==", ">=") or "," in match["rest"]:
            return content, f"skip: `{package_name}` has a bounded or complex constraint in the file"
        try:
            current = packaging.version.Version(match["first_version"])
            target = packaging.version.Version(target_version)
        except packaging.version.InvalidVersion:
            return content, f"skip: `{package_name}` has an unparsable version in the file"
        if current >= target:
            return content, "already"
        new_line = (
            f"{match['pre']}{match['name']}{match['extras'] or ''}{match['gap1']}"
            f"{match['first_op']}{target_version}{match['rest']}{match['post']}\n"
        )
        lines[index] = new_line
        return "".join(lines), "bumped"
    suffix = "" if content.endswith("\n") or not content else "\n"
    return f"{content}{suffix}{package_name}>={target_version}\n", "appended"


async def _fix_requirements_files(
    cwd: anyio.Path,
    plans: list[DependencyFixPlan],
    env: dict[str, str],
    pip_install_arguments: list[str],
) -> tuple[list[str], list[str], list[PinnedVulnerability]]:
    """
    Apply the requirement bumps and floors, validating the resolution of each edited file.

    A file whose `pip install --dry-run` no longer resolves is reverted entirely, the added
    floor pins are returned as the pinned vulnerabilities of the report.
    """
    applied: list[str] = []
    skipped: list[str] = []
    pinned: list[PinnedVulnerability] = []
    by_file: dict[str, list[DependencyFixPlan]] = {}
    for plan in plans:
        by_file.setdefault(plan.file, []).append(plan)

    for file_name, file_plans in sorted(by_file.items()):
        path = cwd / file_name
        if not await path.exists():
            skipped.extend(
                f"{plan.package_name} in `{file_name}`: the file does not exist anymore"
                for plan in file_plans
            )
            continue
        original_content = await path.read_text(encoding="utf-8")
        content = original_content
        edited = False
        file_pinned: list[PinnedVulnerability] = []
        file_bumped: list[str] = []
        for plan in file_plans:
            content, outcome = bump_requirement_content(content, plan.package_name, plan.target_version)
            if outcome.startswith("skip:"):
                skipped.append(
                    f"{plan.package_name} {plan.installed_version} in `{file_name}`:" + outcome[5:]
                )
                continue
            if outcome == "appended":
                edited = True
                file_pinned.extend(
                    PinnedVulnerability(
                        vulnerability=dependency_to_vulnerability_data(vulnerability),
                        pinned_version=plan.target_version,
                    )
                    for vulnerability in plan.vulnerabilities
                )
            elif outcome == "bumped":
                edited = True
                file_bumped.append(
                    f"{plan.package_name} {plan.installed_version} -> {plan.target_version} in `{file_name}`"
                )

        if not edited:
            continue
        await path.write_text(content, encoding="utf-8")
        _, valid, _ = await module_utils.run_timeout(
            [
                "python",
                "-m",
                "pip",
                "install",
                "--dry-run",
                "--ignore-installed",
                *pip_install_arguments,
                f"--requirement={file_name}",
            ],
            env,
            _TIMEOUT_PIP_DRY_RUN,
            f"Requirements resolution check {file_name}",
            f"Requirements resolution failed {file_name}",
            f"Requirements resolution timeout {file_name}",
            cwd,
            error=False,
        )
        if not valid:
            await path.write_text(original_content, encoding="utf-8")
            skipped.append(
                f"`{file_name}`: the fixes do not resolve (`pip install --dry-run` failed), reverted"
            )
            continue
        applied.extend(file_bumped)
        applied.extend(
            f"{item.vulnerability.package_name} pinned >=`{item.pinned_version}` in `{file_name}`"
            for item in file_pinned
        )
        pinned.extend(file_pinned)

    return applied, skipped, pinned


def restore_exact_npm_versions(original: str, updated: str) -> str | None:
    """
    Remove the range prefixes added by npm to the exact pinned versions of a package.json.

    Returns the restored content, or None when no exact version gained a `^` or `~` prefix
    (nothing to do, the file is left untouched to keep the diff minimal).
    """
    try:
        original_json = json.loads(original)
        updated_json = json.loads(updated)
    except json.JSONDecodeError:
        return None
    if not isinstance(original_json, dict) or not isinstance(updated_json, dict):
        return None
    changed = False
    for section_name in ("dependencies", "devDependencies"):
        original_section = original_json.get(section_name)
        updated_section = updated_json.get(section_name)
        if not isinstance(original_section, dict) or not isinstance(updated_section, dict):
            continue
        for package, version in updated_section.items():
            original_version = original_section.get(package)
            if (
                isinstance(version, str)
                and isinstance(original_version, str)
                and original_version[:1] not in ("^", "~", ">", "=", "<")
                and version.startswith(("^", "~"))
            ):
                updated_section[package] = version[1:]
                changed = True
    if not changed:
        return None
    restored = json.dumps(updated_json, indent=2)
    if original.endswith("\n"):
        restored += "\n"
    return restored


async def _fix_npm_project(
    cwd: anyio.Path,
    directory: str,
    plans: list[DependencyFixPlan],
    env: dict[str, str],
) -> tuple[list[str], list[str], list[str]]:
    """
    Fix the vulnerable packages of an npm project with ``npm audit fix``.

    A single ``npm audit fix --package-lock-only --ignore-scripts`` call fixes all the planned
    packages of the ``package-lock.json``: only the lock file is rewritten, no ``node_modules``
    is installed and no lifecycle script is run, the Trivy rescan reads the lock file directly.
    The range prefixes added by npm to the exact pins of the ``package.json`` are removed to keep
    the pinning style of the repository. On failure the project files are reverted and the fixes
    are reported as skipped, and packages with no fix inside the current ranges are left to the
    dependency updates (Renovate), as for the Poetry projects.
    """
    applied: list[str] = []
    skipped: list[str] = []
    logs: list[str] = []
    project_dir = cwd / directory if directory else cwd
    lock_path = project_dir / "package-lock.json"
    package_json_path = project_dir / "package.json"
    if not await lock_path.exists():
        return (
            applied,
            [f"{plan.package_name} in `{plan.file}`: the lock file does not exist anymore" for plan in plans],
            logs,
        )
    original_lock = await lock_path.read_text(encoding="utf-8")
    original_package_json = (
        await package_json_path.read_text(encoding="utf-8") if await package_json_path.exists() else None
    )

    _, success, message = await module_utils.run_timeout(
        ["npm", "audit", "fix", "--package-lock-only", "--ignore-scripts"],
        env,
        _TIMEOUT_NPM_AUDIT,
        f"Npm audit fix {directory or '.'}",
        f"Npm audit fix failed {directory or '.'}",
        f"Npm audit fix timeout {directory or '.'}",
        project_dir,
        error=False,
    )
    if message is not None:
        logs.append(message.to_plain_text())
    if not success:
        await lock_path.write_text(original_lock, encoding="utf-8")
        if original_package_json is not None:
            await package_json_path.write_text(original_package_json, encoding="utf-8")
        skipped.append(f"`{directory or '.'}`: `npm audit fix` failed, the files are reverted")
        return applied, skipped, logs

    if original_package_json is not None:
        updated_package_json = await package_json_path.read_text(encoding="utf-8")
        restored = restore_exact_npm_versions(original_package_json, updated_package_json)
        if restored is not None:
            await package_json_path.write_text(restored, encoding="utf-8")

    if await lock_path.read_text(encoding="utf-8") == original_lock and (
        original_package_json is None
        or await package_json_path.read_text(encoding="utf-8") == original_package_json
    ):
        skipped.extend(
            f"{plan.package_name} {plan.installed_version} in `{plan.file}`:"
            " `npm audit fix` has no fix inside the current ranges,"
            " handled by the dependency updates"
            for plan in plans
        )
        return applied, skipped, logs

    applied.extend(
        f"{plan.package_name} {plan.installed_version} in `{plan.file}`: fixed by `npm audit fix`"
        for plan in plans
    )
    return applied, skipped, logs


# ---------------------------------------------------------------------------
# Gradle (Java) dependency detection, fix and locking bootstrap
# ---------------------------------------------------------------------------

_GRADLE_LOCKING_MARKER = "GHCI security audit: enable Gradle dependency locking"
"""Marker comment added to a build script by the lockfile bootstrap, for idempotency."""


async def _git_ls_files(cwd: anyio.Path, patterns: list[str]) -> list[str]:
    """List the tracked files matching the given git pathspecs, relative to the repository root."""
    command = ["git", "ls-files", *patterns]
    stdout, success, _ = await module_utils.run_timeout(
        command,
        None,
        _TIMEOUT_GIT_LSFILES,
        "List the tracked files",
        "Error listing the tracked files",
        "Timeout listing the tracked files",
        cwd,
        error=False,
    )
    if not success or stdout is None:
        return []
    return sorted({line.strip() for line in stdout.splitlines() if line.strip()})


async def find_gradle_lockfiles(cwd: anyio.Path) -> list[str]:
    """List the committed ``gradle.lockfile`` files of the repository, relative to its root."""
    return await _git_ls_files(cwd, ["gradle.lockfile", "**/gradle.lockfile"])


async def _gradle_lockfiles_on_disk(cwd: anyio.Path) -> list[str]:
    """List the ``gradle.lockfile`` files present on the filesystem, relative to the repository root."""
    return [str(path.relative_to(cwd)) async for path in cwd.glob("**/gradle.lockfile")]


async def _gradle_root_build_file(cwd: anyio.Path) -> tuple[anyio.Path | None, bool]:
    """Get the root build script and whether it uses the Kotlin DSL (``build.gradle.kts``)."""
    for name in ("build.gradle.kts", "build.gradle"):
        path = cwd / name
        if await path.exists():
            return path, name.endswith(".kts")
    return None, False


async def _find_version_catalog(cwd: anyio.Path) -> anyio.Path | None:
    """Get the root Gradle version catalog, ``gradle/libs.versions.toml`` when it exists."""
    path = cwd / "gradle" / "libs.versions.toml"
    return path if await path.exists() else None


async def _select_java_for_gradlew(cwd: anyio.Path, env: dict[str, str]) -> None:
    """Prepend the JDK able to run the project Gradle version to the ``PATH`` of the environment."""
    if not await (cwd / "gradlew").exists():
        return
    stdout, success, _ = await module_utils.run_timeout(
        ["./gradlew", "--version"],
        env,
        _TIMEOUT_GRADLE,
        "Gradle version",
        "Error getting the Gradle version",
        "Timeout getting the Gradle version",
        cwd,
        error=False,
    )
    if not success or stdout is None:
        return
    line = next((item for item in stdout.splitlines() if item.startswith("Gradle ")), "")
    parts = line.split()
    if len(parts) < 2:
        return
    java_path = await find_compatible_java_path(parts[1])
    if java_path is not None:
        env["PATH"] = f"{java_path}:{env['PATH']}"


def _toml_table_bounds(content: str, table: str) -> tuple[int, int] | None:
    """Get the ``(start, end)`` offsets of a ``[table]`` section body, None when it is absent."""
    match = re.search(rf"^\[{re.escape(table)}\][ \t]*$", content, re.MULTILINE)
    if match is None:
        return None
    start = match.end()
    following = re.search(r"^\[", content[start:], re.MULTILINE)
    end = start + following.start() if following is not None else len(content)
    return start, end


def _replace_versions_table_key(content: str, key: str, target: str) -> tuple[str, int]:
    """Replace the value of a ``[versions]`` table key, return the new content and the count."""
    bounds = _toml_table_bounds(content, "versions")
    if bounds is None:
        return content, 0
    start, end = bounds
    section = content[start:end]
    pattern = re.compile(rf'^({re.escape(key)}\s*=\s*")[^"]*(")', re.MULTILINE)
    new_section, count = pattern.subn(rf"\g<1>{target}\g<2>", section, count=1)
    if count == 0:
        return content, 0
    return content[:start] + new_section + content[end:], count


def _replace_catalog_alias_version(content: str, alias: str, target: str) -> tuple[str, int]:
    """Replace a literal ``version = "..."`` of a single-line ``[libraries]`` alias entry."""
    pattern = re.compile(
        rf'(?m)^(\s*{re.escape(alias)}\s*=\s*\{{[^}}\n]*?version\s*=\s*")[^"]*("[^}}\n]*\}}.*)$',
    )
    return pattern.subn(rf"\g<1>{target}\g<2>", content, count=1)


def bump_version_catalog(content: str, coordinate: str, target: str) -> tuple[str, str]:
    """
    Bump a Maven coordinate in a Gradle version catalog content.

    Supports a single-line library entry declaring the coordinate with ``module = "group:artifact"``
    (or ``group``/``name``), referenced either by ``version.ref`` to a ``[versions]`` key or by a
    literal ``version = "..."``. Returns the new content and an outcome among ``bumped``, ``already``,
    ``not-in-catalog`` or a ``skip:`` reason.
    """
    if ":" not in coordinate:
        return content, "skip: invalid Maven coordinate"
    try:
        data = tomllib.loads(content)
    except tomllib.TOMLDecodeError:
        return content, "skip: unparsable version catalog"
    group, artifact = coordinate.split(":", 1)
    libraries = data.get("libraries")
    versions = data.get("versions")
    if not isinstance(libraries, dict):
        return content, "not-in-catalog"
    for alias, entry in libraries.items():
        if not isinstance(entry, dict):
            continue
        module = entry.get("module")
        matches = module == coordinate or (entry.get("group") == group and entry.get("name") == artifact)
        if not matches:
            continue
        version = entry.get("version")
        if isinstance(version, dict):
            ref = version.get("ref")
            if not isinstance(ref, str):
                return content, "skip: the catalog version is a range, not a reference"
            current = versions.get(ref) if isinstance(versions, dict) else None
            if not isinstance(current, str):
                return content, f"skip: the catalog version reference `{ref}` is not a plain string"
            if current == target:
                return content, "already"
            new_content, count = _replace_versions_table_key(content, ref, target)
            if count == 0:
                return content, f"skip: the catalog version key `{ref}` was not found"
            return new_content, "bumped"
        if isinstance(version, str):
            if version == target:
                return content, "already"
            new_content, count = _replace_catalog_alias_version(content, alias, target)
            if count == 0:
                return content, f"skip: the catalog alias `{alias}` version is not on a single line"
            return new_content, "bumped"
        return content, "skip: the catalog library has no version"
    return content, "not-in-catalog"


def bump_gradle_inline(content: str, coordinate: str, installed: str, target: str) -> tuple[str, int]:
    """
    Bump a ``group:artifact:installed`` coordinate in a build script content.

    Replaces every occurrence of the exact ``group:artifact:installed`` coordinate (not bounded by
    other version characters) with ``group:artifact:target``; returns the new content and the count.
    """
    token = f"{re.escape(coordinate)}:{re.escape(installed)}"
    pattern = re.compile(rf"{token}(?![A-Za-z0-9._+-])")
    return pattern.subn(f"{coordinate}:{target}", content)


async def _fix_gradle_projects(
    cwd: anyio.Path,
    plans: list[DependencyFixPlan],
    env: dict[str, str],
) -> tuple[list[str], list[str], list[str]]:
    """
    Bump the vulnerable Maven coordinates in the Gradle sources and regenerate the locks.

    The source of truth (the version catalog, then the inline ``build.gradle(.kts)`` declarations) is
    edited for each planned coordinate, then ``./gradlew --write-locks`` regenerates the
    ``gradle.lockfile`` files; when the wrapper is absent, when no version can be located, or when the
    regeneration fails, the sources are reverted and the plan is reported as skipped. The applied
    fixes are confirmed by the rescan done by the caller.
    """
    applied: list[str] = []
    skipped: list[str] = []
    logs: list[str] = []
    if not plans:
        return applied, skipped, logs
    if not await (cwd / "gradlew").exists():
        return (
            applied,
            [
                f"{plan.package_name} in `{plan.file}`: no Gradle wrapper (gradlew), the locks cannot"
                " be regenerated"
                for plan in plans
            ],
            logs,
        )

    lockfiles: dict[str, str] = {}
    for plan in plans:
        lock_path = cwd / plan.file
        key = str(lock_path)
        if key not in lockfiles and await lock_path.exists():
            lockfiles[key] = await lock_path.read_text(encoding="utf-8")

    catalog_path = await _find_version_catalog(cwd)
    catalog_original = await catalog_path.read_text(encoding="utf-8") if catalog_path is not None else None
    catalog_content = catalog_original
    inline_files: dict[str, list[str]] = {}
    changed_plans: list[DependencyFixPlan] = []

    for plan in plans:
        coordinate = plan.package_name
        handled = False
        if catalog_content is not None:
            catalog_content, outcome = bump_version_catalog(catalog_content, coordinate, plan.target_version)
            if outcome == "bumped":
                changed_plans.append(plan)
                handled = True
            elif outcome == "already":
                handled = True
            elif outcome != "not-in-catalog":
                skipped.append(f"{coordinate} in `{plan.file}`: {outcome}")
                handled = True
        if handled:
            continue
        project_dir = plan.file[: -len("gradle.lockfile")].rstrip("/")
        for build_name in ("build.gradle", "build.gradle.kts"):
            build_path = (cwd / project_dir / build_name) if project_dir else (cwd / build_name)
            key = str(build_path)
            if key not in inline_files:
                if not await build_path.exists():
                    continue
                original = await build_path.read_text(encoding="utf-8")
                inline_files[key] = [original, original]
            new_content, count = bump_gradle_inline(
                inline_files[key][1],
                coordinate,
                plan.installed_version,
                plan.target_version,
            )
            if count:
                inline_files[key][1] = new_content
                changed_plans.append(plan)
                break
        else:
            skipped.append(
                f"{coordinate} {plan.installed_version} in `{plan.file}`: not declared in the version"
                " catalog or the build files, left to the dependency updates"
            )

    catalog_changed = catalog_content is not None and catalog_content != catalog_original
    if not changed_plans and not catalog_changed:
        return applied, skipped, logs

    if catalog_changed and catalog_path is not None:
        await catalog_path.write_text(catalog_content or "", encoding="utf-8")
    for key, (original, current) in inline_files.items():
        if current != original:
            await anyio.Path(key).write_text(current, encoding="utf-8")

    gradle_env = dict(env)
    await _select_java_for_gradlew(cwd, gradle_env)
    _, success, message = await module_utils.run_timeout(
        ["./gradlew", "--write-locks", "dependencies"],
        gradle_env,
        _TIMEOUT_GRADLE_LOCKS,
        "Gradle write-locks",
        "Gradle write-locks failed",
        "Gradle write-locks timeout",
        cwd,
        error=False,
    )
    if message is not None:
        logs.append(message.to_plain_text())

    if not success:
        if catalog_changed and catalog_path is not None:
            await catalog_path.write_text(catalog_original or "", encoding="utf-8")
        for key, (original, current) in inline_files.items():
            if current != original:
                await anyio.Path(key).write_text(original, encoding="utf-8")
        for key, original in lockfiles.items():
            await anyio.Path(key).write_text(original, encoding="utf-8")
        skipped.extend(
            f"{plan.package_name} {plan.installed_version} -> {plan.target_version} in `{plan.file}`:"
            " ./gradlew --write-locks failed, reverted"
            for plan in changed_plans
        )
        return applied, skipped, logs

    applied.extend(
        f"{plan.package_name} {plan.installed_version} -> {plan.target_version} in `{plan.file}`"
        for plan in changed_plans
    )
    return applied, skipped, logs


def gradle_locking_block(is_kotlin: bool) -> str:
    """Build the dependency locking block enabling locking on the resolvable runtime/compile configs."""
    header = f"// {_GRADLE_LOCKING_MARKER}"
    if is_kotlin:
        body = dedent(
            """
            allprojects {
                configurations.configureEach {
                    if (isCanBeResolved && (name == "compileClasspath" || name == "runtimeClasspath")) {
                        resolutionStrategy.activateDependencyLocking()
                    }
                }
            }
            """
        ).strip()
    else:
        body = dedent(
            """
            allprojects {
                configurations.configureEach { configuration ->
                    if (configuration.canBeResolved
                            && (configuration.name == 'compileClasspath' || configuration.name == 'runtimeClasspath')) {
                        configuration.resolutionStrategy.activateDependencyLocking()
                    }
                }
            }
            """
        ).strip()
    return f"{header}\n{body}\n"


async def bootstrap_gradle_lockfile(
    cwd: anyio.Path,
    env: dict[str, str],
) -> tuple[bool, str]:
    """
    Enable targeted Gradle dependency locking and generate the missing lock files.

    When the repository is a Gradle project without any committed ``gradle.lockfile``, a locking block
    is added to the root build script (only the resolvable runtime/compile configurations, default
    lock mode) and ``./gradlew --write-locks`` generates the lock files. Returns whether lock files
    were produced (the caller commits them to a separate, not auto-merged, pull request) and a detail
    message. The build script is reverted when the generation produces no lock file.
    """
    if not await (cwd / "gradlew").exists():
        return False, "not a Gradle project"
    if await find_gradle_lockfiles(cwd):
        return False, "dependency locking already in place"
    build_path, is_kotlin = await _gradle_root_build_file(cwd)
    if build_path is None:
        return False, "no root build.gradle or build.gradle.kts to enable locking"
    original = await build_path.read_text(encoding="utf-8")
    already_injected = _GRADLE_LOCKING_MARKER in original
    content = original if already_injected else f"{original.rstrip()}\n\n{gradle_locking_block(is_kotlin)}"
    if content != original:
        await build_path.write_text(content, encoding="utf-8")

    gradle_env = dict(env)
    await _select_java_for_gradlew(cwd, gradle_env)
    _, success, message = await module_utils.run_timeout(
        ["./gradlew", "--write-locks", "dependencies"],
        gradle_env,
        _TIMEOUT_GRADLE_LOCKS,
        "Gradle write-locks (bootstrap)",
        "Gradle write-locks (bootstrap) failed",
        "Gradle write-locks (bootstrap) timeout",
        cwd,
        error=False,
    )
    generated = await _gradle_lockfiles_on_disk(cwd) if success else []
    if not success or not generated:
        if content != original:
            await build_path.write_text(original, encoding="utf-8")
        detail = message.to_plain_text() if message is not None else "no lock file produced"
        return False, f"the lock files could not be generated: {detail}"
    return True, f"enabled dependency locking, generated {len(generated)} lock file(s)"


async def trivy_filesystem(
    branch: str,
    config: configuration.TrivyFilesystemConfiguration,
    local_config: configuration.TrivyFilesystemConfiguration,
    env: dict[str, str],
    cwd: anyio.Path,
    python_version: str,
) -> TrivyFilesystemResult:
    """
    Audit the Python, npm and Gradle dependencies of a branch with Trivy and fix them.

    The vulnerabilities are scanned with ``trivy fs``, the fixes of the packages at or above
    the fix severity threshold are applied to the ``poetry.lock`` files (conservative lock,
    the pyproject.toml left untouched), to the requirements files (bumped pins and added
    floors, validated with ``pip install --dry-run``), to the npm ``package-lock.json`` files
    (``npm audit fix --package-lock-only --ignore-scripts``, with the Node.js version pinned by
    the repository) and to the Gradle projects (bumped version catalog / build file coordinates,
    locks regenerated with ``./gradlew --write-locks``), then a rescan confirms the fixed
    vulnerabilities. ``python_version`` is the branch Python ``major.minor``: target Python
    versions requiring a newer Python are never pinned.
    """
    result: list[module_utils.Message] = []
    fix_threshold = local_config.get(
        "fix-severity-threshold",
        config.get("fix-severity-threshold", configuration.FIX_SEVERITY_THRESHOLD_DEFAULT),
    )
    files_no_fix = local_config.get("files-no-fix", config.get("files-no-fix", []))
    auto_fix = local_config.get("auto-fix", config.get("auto-fix", configuration.AUTOMATIC_FIX0_DEFAULT))

    before, scan_success = await _trivy_fs_scan(
        env, cwd, ignore_policies=False, message_title=f"Trivy dependencies scan {branch}"
    )
    if not scan_success:
        return TrivyFilesystemResult(
            messages=[
                *result,
                module_utils.HtmlMessage(
                    f"The Trivy dependencies scan failed on branch {branch}, see the logs",
                ),
            ],
            fix_message=None,
            short_messages=["Error while scanning the dependencies"],
            success=False,
            vulnerabilities={},
            fixed_vulnerabilities={},
            pinned_vulnerabilities={},
            skipped_fixes=[],
        )

    # All the scanned severities (low and above, unknown included) are reported, the display
    # filtering is done by the dashboard threshold in the module
    reported = before
    vulnerabilities_before = dependency_file_vulnerabilities(reported)

    counts: dict[str, int] = {}
    for vulnerability in reported:
        counts[vulnerability.severity] = counts.get(vulnerability.severity, 0) + 1

    fix_messages: list[str] = []
    skipped_fixes: list[str] = []
    fix_logs: list[str] = []
    pinned_by_file: dict[str, list[PinnedVulnerability]] = {}
    applied_fixes = 0

    plans: list[DependencyFixPlan] = []
    gradle_plans: list[DependencyFixPlan] = []
    if auto_fix:
        plans, plan_skipped = build_dependency_fix_plans(reported, fix_threshold, files_no_fix)
        skipped_fixes.extend(plan_skipped)
        gradle_plans = [plan for plan in plans if plan.file.endswith("gradle.lockfile")]
        python_plans = [plan for plan in plans if not plan.file.endswith("gradle.lockfile")]
        python_plans, python_skipped = await filter_python_incompatible_plans(python_plans, python_version)
        skipped_fixes.extend(python_skipped)
        plans = python_plans

    if auto_fix and plans:
        poetry_plans: dict[str, list[DependencyFixPlan]] = {}
        npm_plans: dict[str, list[DependencyFixPlan]] = {}
        requirements_plans: list[DependencyFixPlan] = []
        for plan in plans:
            if plan.file.endswith("poetry.lock"):
                directory = plan.file[: -len("poetry.lock")].rstrip("/")
                poetry_plans.setdefault(directory, []).append(plan)
            elif plan.file.endswith("package-lock.json"):
                directory = plan.file[: -len("package-lock.json")].rstrip("/")
                npm_plans.setdefault(directory, []).append(plan)
            else:
                requirements_plans.append(plan)

        for directory, directory_plans in sorted(poetry_plans.items()):
            applied, skipped, logs = await _fix_poetry_project(cwd, directory, directory_plans, env)
            fix_messages.extend(applied)
            skipped_fixes.extend(skipped)
            fix_logs.extend(logs)

        if requirements_plans:
            applied, skipped, pinned = await _fix_requirements_files(
                cwd,
                requirements_plans,
                env,
                local_config.get("pip-install-arguments", config.get("pip-install-arguments", [])),
            )
            fix_messages.extend(applied)
            skipped_fixes.extend(skipped)
            for item in pinned:
                pinned_by_file.setdefault(item.vulnerability.file, []).append(item)

        if npm_plans:
            await _select_node_version(env, cwd)
            for directory, directory_plans in sorted(npm_plans.items()):
                applied, skipped, logs = await _fix_npm_project(cwd, directory, directory_plans, env)
                fix_messages.extend(applied)
                skipped_fixes.extend(skipped)
                fix_logs.extend(logs)

    if auto_fix and gradle_plans:
        applied, skipped, logs = await _fix_gradle_projects(cwd, gradle_plans, env)
        fix_messages.extend(applied)
        skipped_fixes.extend(skipped)
        fix_logs.extend(logs)

    fix_message: module_utils.HtmlMessage | None = None
    lines = []
    if not auto_fix:
        lines.append(
            "The automatic fixes are disabled by the `trivy.filesystem.auto-fix` configuration, "
            "the fixable vulnerabilities are only reported."
        )
    lines.extend(f"Applied: {html.escape(message)}" for message in fix_messages)
    lines.extend(f"Skipped: {html.escape(message)}" for message in skipped_fixes)
    if lines or fix_logs:
        html_body = "<br>\n".join(lines)
        if fix_logs:
            html_body += f"\n<pre>{html.escape('\n'.join(fix_logs[:200]))}</pre>"
        fix_message = module_utils.HtmlMessage(html_body)
        fix_message.title = "Trivy dependencies fix output"

    success = True
    vulnerabilities_after = vulnerabilities_before
    fixed: dict[str, list[VulnerabilityData]] = {}
    if fix_messages:
        after_vulnerabilities, rescan_success = await _trivy_fs_scan(
            env,
            cwd,
            ignore_policies=False,
            message_title=f"Trivy dependencies rescan {branch}",
        )
        if not rescan_success:
            success = False
        else:
            vulnerabilities_after = dependency_file_vulnerabilities(after_vulnerabilities)
            fixed = fixed_dependency_vulnerabilities(vulnerabilities_before, vulnerabilities_after)
            applied_fixes = sum(len(items) for items in fixed.values())

    short_messages = [
        f"{number} {severity} vulnerabilities" for severity, number in sorted(counts.items()) if number
    ]
    if applied_fixes:
        fixed_word = "vulnerability" if applied_fixes == 1 else "vulnerabilities"
        short_messages.append(f"{applied_fixes} {fixed_word} fixed")
    if pinned_by_file:
        short_messages.append(
            f"{sum(len(items) for items in pinned_by_file.values())} fixes pinned",
        )
    if skipped_fixes:
        short_messages.append(f"{len(skipped_fixes)} fixes skipped")

    return TrivyFilesystemResult(
        messages=result,
        fix_message=fix_message,
        short_messages=short_messages,
        success=success,
        vulnerabilities=vulnerabilities_after,
        fixed_vulnerabilities=fixed,
        pinned_vulnerabilities=dict(sorted(pinned_by_file.items())),
        skipped_fixes=skipped_fixes,
    )


async def trivy_filesystem_ignored(
    branch: str,
    env: dict[str, str],
    cwd: anyio.Path,
) -> dict[str, list[VulnerabilityData]]:
    """Scan with the ignore policies bypassed and return the file-grouped vulnerability data."""
    all_vulnerabilities, scan_success = await _trivy_fs_scan(
        env,
        cwd,
        ignore_policies=True,
        message_title=f"Trivy dependencies full scan {branch}",
    )
    if not scan_success:
        return {}
    return dependency_file_vulnerabilities(all_vulnerabilities)


async def find_trivyignore_files(cwd: anyio.Path) -> list[anyio.Path]:
    """Find all the .trivyignore.yaml files in the repository."""
    command = ["git", "ls-files", ".trivyignore.yaml", "**/.trivyignore.yaml"]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        cwd=cwd,
    )  # nosec
    async with asyncio.timeout(settings.audit.timeouts.git_lsfiles.total_seconds()):
        stdout, _ = await proc.communicate()
    result = stdout.decode().strip()
    return [cwd / file for file in result.split("\n") if file] if result else []


async def parse_trivyignore_reasons(trivyignore_file: anyio.Path) -> dict[str, str]:
    """Parse a .trivyignore.yaml file and return a dict mapping vulnerability IDs to reasons."""
    if not await trivyignore_file.exists():
        return {}
    data = yaml.safe_load(await trivyignore_file.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    reasons: dict[str, str] = {}
    for entry in data.get("vulnerabilities", []):
        if not isinstance(entry, dict):
            continue
        vulnerability_id = entry.get("id")
        if not isinstance(vulnerability_id, str):
            continue
        reason = entry.get("reason")
        expire = entry.get("expire")
        parts = [str(reason)] if isinstance(reason, str) else []
        if expire is not None:
            parts.append(f"until {expire}")
        reasons[vulnerability_id] = ", ".join(parts) if parts else ""
    return reasons
