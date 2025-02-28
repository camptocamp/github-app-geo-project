"""The auditing functions."""

import asyncio
import datetime
import io
import json
import logging
import os.path
import subprocess
from pathlib import Path
from typing import NamedTuple

import aiofiles
import apt_repo
import debian_inspector.version
import security_md
import yaml  # nosec

from github_app_geo_project import models, utils
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.module.audit import configuration

_LOGGER = logging.getLogger(__name__)


async def snyk(
    branch: str,
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    logs_url: str,
    env: dict[str, str],
) -> tuple[list[module_utils.Message], module_utils.HtmlMessage | None, list[str], bool]:
    """
    Audit the code with Snyk.

    Return:
    ------
        the output messages (Install errors, high of upgradable vulnerabilities),
        the message of the fix commit,
        the dashboard's message (with resume of the vulnerabilities),
        is on success (errors: vulnerability that can be fixed by upgrading the dependency).
    """
    result: list[module_utils.Message] = []

    env["PATH"] = f"{env['HOME']}/.local/bin:{env['PATH']}"

    await _select_java_version(config, local_config, env)

    _LOGGER.debug("Updated path: %s", env["PATH"])

    await _install_requirements_dependencies(config, local_config, result, env)

    command = ["pip", "freeze"]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )  # nosec
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    message = module_utils.AnsiProcessMessage.from_async_artifacts(command, proc, stdout, stderr)
    message.title = "Pip freeze"
    _LOGGER.info(message)

    await _install_pipenv_dependencies(config, local_config, result, env)
    await _install_poetry_dependencies(config, local_config, result, env)

    env["FORCE_COLOR"] = "true"
    env_no_debug = {**env}
    env["DEBUG"] = "*snyk*"  # debug mode

    await _snyk_monitor(branch, config, local_config, result, env)

    (
        high_vulnerabilities,
        fixable_vulnerabilities,
        fixable_vulnerabilities_summary,
        fixable_files_npm,
        vulnerabilities_in_requirements,
    ) = await _snyk_test(branch, config, local_config, result, env_no_debug)

    snyk_fix_success, snyk_fix_message = await _snyk_fix(
        branch,
        config,
        local_config,
        logs_url,
        result,
        env_no_debug,
        env,
        fixable_vulnerabilities_summary,
        vulnerabilities_in_requirements,
    )
    npm_audit_fix_message, npm_audit_fix_success = await _npm_audit_fix(fixable_files_npm, result)
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
    fix_success = (
        True if len(fixable_vulnerabilities_summary) == 0 else (snyk_fix_success and npm_audit_fix_success)
    )

    command = ["git", "diff", "--quiet"]
    diff_proc = await asyncio.create_subprocess_exec(*command)
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
    if diff_proc.returncode != 0:
        (
            high_vulnerabilities,
            fixable_vulnerabilities,
            fixable_vulnerabilities_summary,
            fixable_files_npm,
            vulnerabilities_in_requirements,
        ) = await _snyk_test(branch, config, local_config, result, env_no_debug)

    return_message = [
        *[f"{number} {severity} vulnerabilities" for severity, number in high_vulnerabilities.items()],
        *[
            f"{number} {severity} vulnerabilities can be fixed"
            for severity, number in fixable_vulnerabilities.items()
        ],
        *([] if fix_success else ["Error while fixing the vulnerabilities"]),
    ]

    return result, fix_message, return_message, fix_success


async def _select_java_version(
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    env: dict[str, str],
) -> None:
    if not Path("gradlew").exists():
        return

    command = ["./gradlew", "--version"]
    proc = await asyncio.create_subprocess_exec(  # nosec
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
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
            10,
            "Gradle version",
            "Error on getting Gradle version",
            "Timeout on getting Gradle version",
        )
        return

    env["PATH"] = f"{java_path_for_gradle[minor_gradle_version]}:{env['PATH']}"


async def _install_requirements_dependencies(
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    result: list[module_utils.Message],
    env: dict[str, str],
) -> None:
    command = ["git", "ls-files", "requirements.txt", "*/requirements.txt"]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
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
                int(os.environ.get("GHCI_PYTHON_INSTALL_TIMEOUT", "600")),
                f"Dependencies installed from {file}",
                f"Error while installing the dependencies from {file}",
                f"Timeout while installing the dependencies from {file}",
            )
            if proc_message is not None:
                result.append(proc_message)


async def _install_pipenv_dependencies(
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    result: list[module_utils.Message],
    env: dict[str, str],
) -> None:
    command = ["git", "ls-files", "Pipfile", "*/Pipfile"]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
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
            directory = Path(file).resolve().parent

            _, _, proc_message = await module_utils.run_timeout(
                [
                    "pipenv",
                    "sync",
                    *local_config.get("pipenv-sync-arguments", config.get("pipenv-sync-arguments", [])),
                ],
                env,
                int(os.environ.get("GHCI_PYTHON_INSTALL_TIMEOUT", "600")),
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
) -> None:
    command = ["git", "ls-files", "poetry.lock", "*/poetry.lock"]
    proc = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
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
                    "poetry",
                    "install",
                    *local_config.get("poetry-install-arguments", config.get("poetry-install-arguments", [])),
                ],
                env,
                int(os.environ.get("GHCI_PYTHON_INSTALL_TIMEOUT", "600")),
                f"Dependencies installed from {file}",
                f"Error while installing the dependencies from {file}",
                f"Timeout while installing the dependencies from {file}",
                Path(file).resolve().parent,
            )
            if proc_message is not None:
                result.append(proc_message)


async def _snyk_monitor(
    branch: str,
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    result: list[module_utils.Message],
    env: dict[str, str],
) -> None:
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

    _, _, message = await module_utils.run_timeout(
        command,
        env,
        int(os.environ.get("GHCI_SNYK_TIMEOUT", "300")),
        "Project monitored",
        "Error while monitoring the project",
        "Timeout while monitoring the project",
    )
    if message is not None:
        result.append(message)


async def _snyk_test(
    branch: str,
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    result: list[module_utils.Message],
    env_no_debug: dict[str, str],
) -> tuple[dict[str, int], dict[str, int], dict[str, str], dict[str, set[str]], bool]:
    # Test with human output
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
        int(os.environ.get("GHCI_SNYK_TIMEOUT", "300")),
        "Snyk test (human)",
        "Error while testing the project",
        "Timeout while testing the project",
    )

    command = [
        "snyk",
        "test",
        "--json",
        *local_config.get(
            "test-arguments",
            config.get("test-arguments", configuration.SNYK_TEST_ARGUMENTS_DEFAULT),
        ),
    ]
    test_json_str, _, message = await module_utils.run_timeout(
        command,
        env_no_debug,
        int(os.environ.get("GHCI_SNYK_TIMEOUT", "300")),
        "Snyk test",
        "Error while testing the project",
        "Timeout while testing the project",
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

        class _Vulnerability(NamedTuple):
            link: str
            title: str
            identifiers: list[str]
            paths: list[str]

        vulnerabilities: dict[str, _Vulnerability] = {}

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
            title = " ".join(
                [
                    f"[{vuln['severity'].upper()}]",
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

            if title not in vulnerabilities:
                vulnerabilities[title] = _Vulnerability(
                    f'<a href="https://security.snyk.io/vuln/{vuln["id"]}">{vuln["id"]}</a>',
                    vuln["title"],
                    [
                        f"{identifier}: {', '.join(values)}"
                        for identifier, values in vuln.get("identifiers", {}).items()
                    ],
                    [],
                )
            vulnerabilities[title].paths.append(
                " > ".join([row.get("displayTargetFile", "-"), *vuln["from"]]),
            )

        for title, vulnerability in vulnerabilities.items():
            message = module_utils.HtmlMessage(
                "<br>".join(
                    [
                        f"{vulnerability.title} [{vulnerability.link}]",
                        *vulnerability.identifiers,
                        "",
                        *vulnerability.paths,
                    ],
                ),
                title,
            )
            _LOGGER.warning(message)
            result.append(message)
    _LOGGER.debug("End parsing the vulnerabilities")
    return (
        high_vulnerabilities,
        fixable_vulnerabilities,
        fixable_vulnerabilities_summary,
        fixable_files_npm,
        vulnerabilities_in_requirements,
    )


async def _snyk_fix(
    branch: str,
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
        10,
        "Poetry version",
        "Error while getting the Poetry version",
        "Timeout while getting the Poetry version",
        error=False,
    )

    snyk_fix_success = True
    snyk_fix_message = None
    command = ["git", "reset", "--hard"]
    proc = await asyncio.create_subprocess_exec(*command)
    await asyncio.wait_for(proc.communicate(), timeout=30)
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
            int(os.environ.get("GHCI_SNYK_FIX_TIMEOUT", os.environ.get("GHCI_SNYK_TIMEOUT", "300"))),
            "Snyk fix",
            "Error while fixing the project",
            "Timeout while fixing the project",
        )
        if message is not None:
            result.append(message)
        if fix_message:
            snyk_fix_message = module_utils.AnsiMessage(fix_message.strip())
        if not snyk_fix_success:
            await module_utils.run_timeout(
                command,
                env_debug,
                int(os.environ.get("GHCI_SNYK_FIX_TIMEOUT", os.environ.get("GHCI_SNYK_TIMEOUT", "300"))),
                "Snyk fix (debug)",
                "Error while fixing the project (debug)",
                "Timeout while fixing the project (debug)",
            )

            cwd = module_utils.get_cwd()
            project = "-" if cwd is None else Path(cwd).name
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

    return snyk_fix_success, snyk_fix_message


async def _npm_audit_fix(
    fixable_files_npm: dict[str, set[str]],
    result: list[module_utils.Message],
) -> tuple[str, bool]:
    messages: set[str] = set()
    fix_success = True
    for package_lock_file_name, file_messages in fixable_files_npm.items():
        directory = Path(package_lock_file_name).absolute().parent
        messages.update(file_messages)
        _LOGGER.debug("Fixing vulnerabilities in %s with npm audit fix", package_lock_file_name)
        command = ["npm", "audit", "fix"]
        _, success, message = await module_utils.run_timeout(
            command,
            os.environ.copy(),
            int(os.environ.get("GHCI_SNYK_TIMEOUT", "300")),
            "Npm audit fix",
            "Error while fixing the project",
            "Timeout while fixing the project",
            directory,
        )
        if message is not None:
            result.append(message)
        _LOGGER.debug("Fixing version in %s", package_lock_file_name)
        # Remove the add '~' in the version in the package.json
        async with aiofiles.open(directory / "package.json", encoding="utf-8") as package_file:
            package_json = json.load(await package_file.read())
            for dependencies_type in ("dependencies", "devDependencies"):
                for package, version in package_json.get(dependencies_type, {}).items():
                    if version.startswith("^"):
                        package_json[dependencies_type][package] = version[1:]
        async with aiofiles.open(directory / "package.json", "w", encoding="utf-8") as package_file:
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
_SOURCES = {}
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
    global _GENERATION_TIME  # pylint: disable=global-statement
    if (
        _GENERATION_TIME is None
        or datetime.datetime.now(datetime.UTC)
        - utils.parse_duration(os.environ.get("GHCI_DPKG_CACHE_DURATION", "3h"))
        > _GENERATION_TIME
    ):
        _PACKAGE_VERSION.clear()
        _SOURCES.clear()
        _GENERATION_TIME = datetime.datetime.now(datetime.UTC)
    if package not in _PACKAGE_VERSION:
        dist = package.split("/")[0]
        await asyncio.to_thread(_get_sources, dist, config, local_config)
    if package not in _PACKAGE_VERSION:
        _LOGGER.warning("No version found for %s", package)
    return str(_PACKAGE_VERSION.get(package))


async def dpkg(
    config: configuration.DpkgConfiguration,
    local_config: configuration.DpkgConfiguration,
) -> None:
    """Update the version of packages in the file .github/dpkg-versions.yaml or ci/dpkg-versions.yaml."""
    ci_dpkg_versions_filename = Path(".github/dpkg-versions.yaml")
    github_dpkg_versions_filename = Path("ci/dpkg-versions.yaml")

    if not ci_dpkg_versions_filename.exists() and not github_dpkg_versions_filename.exists():
        _LOGGER.warning("The file .github/dpkg-versions.yaml or ci/dpkg-versions.yaml does not exist")

    dpkg_versions_filename = (
        github_dpkg_versions_filename if github_dpkg_versions_filename.exists() else ci_dpkg_versions_filename
    )

    with dpkg_versions_filename.open(encoding="utf-8") as versions_file:
        versions_config = yaml.load(versions_file, Loader=yaml.SafeLoader)
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
                        "Error while parsing the current version %s of the package %s: %s",
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
                        "Error while parsing the new version %s of the package %s: %s",
                        version,
                        package_full,
                        exception,
                    )

    with dpkg_versions_filename.open("w", encoding="utf-8") as versions_file:
        yaml.dump(versions_config, versions_file, Dumper=yaml.SafeDumper)
