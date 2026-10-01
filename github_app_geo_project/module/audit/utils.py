# Copyright (c) 2026, Camptocamp SA

"""The auditing functions."""

import asyncio
import datetime
import html
import io
import json
import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any, NamedTuple

import aiohttp
import anyio
import apt_repo
import debian_inspector.version
import security_md
import yaml  # nosec

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
_TIMEOUT_GIT_LSFILES = settings.audit.timeouts.git_lsfiles
_TIMEOUT_PYTHON_INSTALL = settings.audit.timeouts.python_install
_TIMEOUT_SNYK = settings.audit.timeouts.snyk
_TIMEOUT_SNYK_FIX = settings.audit.timeouts.snyk_fix
_TIMEOUT_POETRY_VERSION = settings.audit.timeouts.poetry_version
_TIMEOUT_NPM_AUDIT = settings.audit.timeouts.npm_audit
_TIMEOUT_NODE_INSTALL = settings.audit.timeouts.node_install


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


# Map Snyk package managers to GitHub advisory ecosystems
ECOSYSTEM_MAP: dict[str, str] = {
    "pip": "pip",
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
    ignore_policy: bool = False,
) -> tuple[
    list[module_utils.Message],
    module_utils.HtmlMessage | None,
    list[str],
    bool,
    dict[str, list[VulnerabilityData]],
    dict[str, list[VulnerabilityData]],
]:
    """
    Audit the code with Snyk.

    Return:
    ------
        the output messages (Install errors, high of upgradable vulnerabilities),
        the message of the fix commit,
        the dashboard's message (with resume of the vulnerabilities),
        is on success (errors: vulnerability that can be fixed by upgrading the dependency).
        the file-grouped vulnerability data for dashboard display and advisory creation.
        the file-grouped vulnerability data fixed by this run.
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

        monitor_started_at = datetime.datetime.now(datetime.UTC)
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
        else:
            # The fix did not change anything, the vulnerabilities are the ones of the first scan.
            file_vulnerabilities = vulnerabilities_before_fix
            fixed = {}

        return_message = [
            *[f"{number} {severity} vulnerabilities" for severity, number in high_vulnerabilities.items()],
            *[
                f"{number} {severity} vulnerabilities can be fixed"
                for severity, number in fixable_vulnerabilities.items()
            ],
            *([] if not fix_has_errors else ["Error while fixing the vulnerabilities"]),
        ]

        return result, fix_message, return_message, fix_success, file_vulnerabilities, fixed
    finally:
        await _cleanup_poetry_envs(poetry_install_dirs, env)


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
            "Gradle version %s is not in the configuration: %s.",
            minor_gradle_version,
            ", ".join(java_path_for_gradle.keys()),
        )
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

_SNYK_MONITOR_SKEW = datetime.timedelta(minutes=10)
"""Tolerance applied on the monitor run start when looking for stale projects (clock desynchronization)."""

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


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
) -> dict[str, Any] | None:
    """
    Perform a GET request on the Snyk REST API.

    Returns None on error: the projects cleanup is best-effort and must not fail the audit job.
    """
    async with session.get(url, params=params) as response:
        if not response.ok:
            _LOGGER.warning(
                "Snyk API error on %s: %s %s",
                url,
                response.status,
                (await response.text())[:500],
            )
            return None
        data: dict[str, Any] = json.loads(await response.read())
        return data


async def _snyk_api_get_all(
    session: aiohttp.ClientSession,
    api_url: str,
    path: str,
    params: dict[str, str] | list[tuple[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """Get all the entries of a paginated Snyk REST API collection, empty list on error."""
    entries: list[dict[str, Any]] = []
    url: str | None = f"{api_url}/rest{path}"
    request_params: dict[str, str] | list[tuple[str, str]] | None = [
        *(params.items() if isinstance(params, dict) else params or []),
        ("version", _SNYK_API_VERSION),
        ("limit", "100"),
    ]
    while url is not None:
        data = await _snyk_api_get(session, url, request_params)
        if data is None:
            return []
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
) -> list[dict[str, Any]]:
    """Delete Snyk projects, returns the successfully deleted ones."""

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
    return [project for project in deleted if project is not None]


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
    monitored_before: datetime.datetime,
    result: list[module_utils.Message],
) -> None:
    """
    Delete the Snyk projects of a reference that were not refreshed by the latest monitor run.

    This removes the projects of the dependency files that are not scanned anymore,
    a reference grouping disappears from the Snyk UI when it does not contain any project.
    """
    api_config = _snyk_api_config()
    if api_config is None:
        return
    token, api_url = api_config
    async with _snyk_api_session(token) as session:
        targets = await _snyk_resolve_targets(session, api_url, owner, repository)
        if targets is None:
            return
        org_id, target_ids = targets
        projects = await _snyk_list_projects(
            session,
            api_url,
            org_id,
            target_ids,
            [
                ("target_reference", branch),
                ("cli_monitored_before", monitored_before.isoformat()),
            ],
        )
        stale_projects = [
            project for project in projects if (project.get("attributes") or {}).get("origin") == "cli"
        ]
        if not stale_projects:
            _LOGGER.debug("No stale Snyk project to remove for the reference %s", branch)
            return
        deleted = await _snyk_delete_projects(session, api_url, org_id, stale_projects)
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


async def snyk_cleanup_removed_references(
    owner: str, repository: str, known_versions: list[str]
) -> list[str]:
    """
    Delete all the Snyk projects whose target reference is not a supported version anymore.

    Emptying a reference makes it disappear from the Snyk UI,
    returns the report entries of the removed references.
    """
    api_config = _snyk_api_config()
    if api_config is None:
        return []
    token, api_url = api_config
    async with _snyk_api_session(token) as session:
        targets = await _snyk_resolve_targets(session, api_url, owner, repository)
        if targets is None:
            return []
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
            return []
        deleted = await _snyk_delete_projects(session, api_url, org_id, removed_projects)
        references: dict[str, int] = {}
        for project in deleted:
            reference = str((project.get("attributes") or {}).get("target_reference") or "")
            references[reference] = references.get(reference, 0) + 1
        return [
            f"Snyk reference `{reference}` ({number} projects)"
            for reference, number in sorted(references.items())
        ]


async def snyk_cleanup_stale_projects_by_age(owner: str, repository: str) -> list[str]:
    """
    Delete the Snyk projects of the repository that were not re-monitored for too long.

    This removes, independently of the monitor runs result, the leftovers of dependency
    files that are not scanned anymore (e.g. projects created with random names by past
    runs) and the projects of references whose monitor fails for a long time.
    Returns the report entries of the removed projects.
    """
    api_config = _snyk_api_config()
    if api_config is None:
        return []
    token, api_url = api_config
    stale_age = settings.audit.snyk_api_stale_age
    async with _snyk_api_session(token) as session:
        targets = await _snyk_resolve_targets(session, api_url, owner, repository)
        if targets is None:
            return []
        org_id, target_ids = targets
        monitored_before = datetime.datetime.now(datetime.UTC) - stale_age
        projects = await _snyk_list_projects(
            session,
            api_url,
            org_id,
            target_ids,
            [("cli_monitored_before", monitored_before.isoformat())],
        )
        stale_projects = [
            project for project in projects if (project.get("attributes") or {}).get("origin") == "cli"
        ]
        if not stale_projects:
            _LOGGER.debug(
                "No stale Snyk project by age to remove for the repository %s/%s", owner, repository
            )
            return []
        deleted = await _snyk_delete_projects(session, api_url, org_id, stale_projects)
        if not deleted:
            return []
        _LOGGER.info(
            "Removed %s stale Snyk project(s) of %s/%s not monitored since %s days",
            len(deleted),
            owner,
            repository,
            stale_age.days,
        )
        return [f"{len(deleted)} stale Snyk project(s) not monitored since {stale_age.days} days"]


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
            date = datetime.datetime.strptime(row[date_index], "%d/%m/%Y").replace(tzinfo=datetime.UTC)
            if date < datetime.datetime.now(datetime.UTC):
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
        or datetime.datetime.now(datetime.UTC) - settings.audit.dpkg_cache_duration > _GENERATION_TIME
    ):
        _PACKAGE_VERSION.clear()
        _SOURCES.clear()
        _GENERATION_TIME = datetime.datetime.now(datetime.UTC)
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
