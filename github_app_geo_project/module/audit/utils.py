"""
The auditing functions.
"""

import asyncio
import datetime
import json
import logging
import os.path
import subprocess  # nosec

import apt_repo
import c2cciutils.security
import debian_inspector.version
import yaml  # nosec

from github_app_geo_project import models, utils
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.module.audit import configuration

_LOGGER = logging.getLogger(__name__)


async def _run_timeout(
    command: list[str],
    env: dict[str, str] | None,
    timeout: int,
    success_message: str,
    error_message: str,
    timeout_message: str,
    error_messages: list[module_utils.Message],
    cwd: str | None = None,
) -> tuple[str | None, bool]:
    async_proc = None
    try:
        async with asyncio.timeout(timeout):
            async_proc = await asyncio.create_subprocess_exec(
                *command,
                cwd=cwd or os.getcwd(),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await async_proc.communicate()
            assert async_proc.returncode is not None
            message: module_utils.Message = module_utils.AnsiProcessMessage(
                command, async_proc.returncode, stdout.decode(), stderr.decode()
            )
            success = async_proc.returncode == 0
            if success:
                message.title = success_message
                _LOGGER.debug(message)
            else:
                message.title = error_message
                _LOGGER.warning(message)
                error_messages.append(message)
            return stdout.decode(), success
    except FileNotFoundError as exception:
        _LOGGER.exception("%s not found: %s", command[0], exception)
        proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
            ["find", "/", "-name", command[0]],
            capture_output=True,
            encoding="utf-8",
            timeout=30,
        )
        message = module_utils.ansi_proc_message(proc)
        message.title = f"Find {command[0]}"
        _LOGGER.debug(message)
        return None, False
    except asyncio.TimeoutError as exception:
        if async_proc:
            async_proc.kill()
            message = module_utils.AnsiProcessMessage(
                command,
                None,
                "" if async_proc.stdout is None else (await async_proc.stdout.read()).decode(),
                "" if async_proc.stderr is None else (await async_proc.stderr.read()).decode(),
                error=str(exception),
            )
            message.title = timeout_message
            _LOGGER.warning(message)
            error_messages.append(message)
            return None, False
        else:
            raise


async def snyk(
    branch: str,
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    logs_url: str,
) -> tuple[list[module_utils.Message], module_utils.Message | None, list[str], bool]:
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

    env = os.environ.copy()
    env["PATH"] = f'{env["HOME"]}/.local/bin:{env["PATH"]}'
    _LOGGER.debug("Updated path: %s", env["PATH"])

    await _install_requirements_dependencies(config, local_config, result, env)
    await _install_pipenv_dependencies(config, local_config, result, env)
    await _install_poetry_dependencies(config, local_config, result, env)

    env = {**os.environ}
    env["FORCE_COLOR"] = "true"
    env_no_debug = {**env}
    env["DEBUG"] = "*snyk*"  # debug mode

    await _snyk_monitor(branch, config, local_config, result, env)

    high_vulnerabilities, fixable_vulnerabilities, fixable_vulnerabilities_summary, fixable_files_npm = (
        await _snyk_test(branch, config, local_config, result, env_no_debug)
    )

    snyk_fix_success, snyk_fix_message = await _snyk_fix(
        branch,
        config,
        local_config,
        logs_url,
        result,
        env_no_debug,
        fixable_vulnerabilities,
        fixable_vulnerabilities_summary,
    )
    npm_audit_fix_message, npm_audit_fix_success = await _npm_audit_fix(fixable_files_npm, result)
    fix_message: module_utils.Message | None = None
    if snyk_fix_message is None:
        if npm_audit_fix_message:
            fix_message = module_utils.HtmlMessage(npm_audit_fix_message)
            fix_message.title = "Npm audit fix"
    else:
        fix_message = snyk_fix_message
        if npm_audit_fix_message:
            assert isinstance(fix_message, module_utils.HtmlMessage)
            fix_message.html = f"{fix_message.html}<br>\n<br>\n{npm_audit_fix_message}"
    fix_success = snyk_fix_success and npm_audit_fix_success

    return_message = [
        *[f"{number} {severity} vulnerabilities" for severity, number in high_vulnerabilities.items()],
        *[
            f"{number} {severity} vulnerabilities can be fixed"
            for severity, number in fixable_vulnerabilities.items()
        ],
        *([] if snyk_fix_success else ["Error while fixing the vulnerabilities"]),
    ]

    return result, fix_message, return_message, fix_success


async def _install_requirements_dependencies(
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    result: list[module_utils.Message],
    env: dict[str, str],
) -> None:
    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        ["git", "ls-files", "requirements.txt", "*/requirements.txt"],
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    if proc.returncode != 0:
        message = module_utils.ansi_proc_message(proc)
        message.title = "Error in ls-files"
        _LOGGER.warning(message)
        result.append(message)
    else:
        for file in proc.stdout.strip().split("\n"):
            if not file:
                continue
            if file in local_config.get("files-no-install", config.get("files-no-install", [])):
                continue

            await _run_timeout(
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
                result,
            )


async def _install_pipenv_dependencies(
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    result: list[module_utils.Message],
    env: dict[str, str],
) -> None:
    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        ["git", "ls-files", "Pipfile", "*/Pipfile"], capture_output=True, encoding="utf-8", timeout=30
    )
    if proc.returncode != 0:
        message = module_utils.ansi_proc_message(proc)
        message.title = "Error in ls-files"
        _LOGGER.warning(message)
        result.append(message)
    else:
        for file in proc.stdout.strip().split("\n"):
            if not file:
                continue
            if file in local_config.get("files-no-install", config.get("files-no-install", [])):
                continue
            directory = os.path.dirname(os.path.abspath(file))

            await _run_timeout(
                [
                    "pipenv",
                    "install",
                    *local_config.get("pipenv-sync-arguments", config.get("pipenv-sync-arguments", [])),
                ],
                env,
                int(os.environ.get("GHCI_PYTHON_INSTALL_TIMEOUT", "600")),
                f"Dependencies installed from {file}",
                f"Error while installing the dependencies from {file}",
                f"Timeout while installing the dependencies from {file}",
                result,
                directory,
            )


async def _install_poetry_dependencies(
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    result: list[module_utils.Message],
    env: dict[str, str],
) -> None:
    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        ["git", "ls-files", "poetry.lock", "*/poetry.lock"],
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    if proc.returncode != 0:
        message = module_utils.ansi_proc_message(proc)
        message.title = "Error in ls-files"
        _LOGGER.warning(message)
        result.append(message)
    else:
        for file in proc.stdout.strip().split("\n"):
            if not file:
                continue
            if file in local_config.get("files-no-install", config.get("files-no-install", [])):
                continue

            await _run_timeout(
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
                result,
                os.path.dirname(os.path.abspath(file)),
            )


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
            "monitor-arguments", config.get("monitor-arguments", configuration.SNYK_MONITOR_ARGUMENTS_DEFAULT)
        ),
    ]
    local_monitor_config = local_config.get("monitor", {})
    monitor_config = config.get("monitor", {})
    if "project-environment" in local_monitor_config or "project-environment" in monitor_config:
        command.append(
            f"--project-environment={','.join(local_monitor_config.get('project-environment', monitor_config.get('project-environment', [])))}"
        )
    if "project-lifecycle" in local_monitor_config or "project-lifecycle" in monitor_config:
        command.append(
            f"--project-lifecycle={','.join(local_monitor_config.get('project-lifecycle', monitor_config.get('project-lifecycle', [])))}"
        )
    if (
        "project-business-criticality" in local_monitor_config
        or "project-business-criticality" in monitor_config
    ):
        command.append(
            f"--project-business-criticality={','.join(local_monitor_config.get('project-business-criticality', monitor_config.get('project-business-criticality', [])))}"
        )
    if "project-tags" in local_monitor_config or "project-tags" in monitor_config:
        command.append(
            f"--project-tags={','.join(['='.join(tag) for tag in local_monitor_config.get('project-tags', monitor_config.get('project-tags', {}))])}"
        )

    await _run_timeout(
        command,
        env,
        int(os.environ.get("GHCI_SNYK_TIMEOUT", "300")),
        "Project monitored",
        "Error while monitoring the project",
        "Timeout while monitoring the project",
        result,
    )


async def _snyk_test(
    branch: str,
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    result: list[module_utils.Message],
    env_no_debug: dict[str, str],
) -> tuple[dict[str, int], dict[str, int], dict[str, str], dict[str, set[str]]]:
    command = [
        "snyk",
        "test",
        "--json",
        *local_config.get(
            "test-arguments", config.get("test-arguments", configuration.SNYK_TEST_ARGUMENTS_DEFAULT)
        ),
    ]
    test_json_str, success = await _run_timeout(
        command,
        env_no_debug,
        int(os.environ.get("GHCI_SNYK_TIMEOUT", "300")),
        "Snyk test",
        "Error while testing the project",
        "Timeout while testing the project",
        result,
    )
    if not success:
        raise ValueError("Error while testing the project")

    if test_json_str:
        message = module_utils.HtmlMessage(utils.format_json_str(test_json_str))
        message.title = "Snyk test JSON output"
        _LOGGER.debug(message)
    else:
        _LOGGER.error("Snyk test JSON returned nothing on project %s branch %s", os.getcwd(), branch)

    test_json = json.loads(test_json_str) if test_json_str else []

    if not isinstance(test_json, list):
        test_json = [test_json]

    high_vulnerabilities: dict[str, int] = {}
    fixable_vulnerabilities: dict[str, int] = {}
    fixable_vulnerabilities_summary: dict[str, str] = {}
    fixable_files_npm: dict[str, set[str]] = {}
    for row in test_json:
        message = module_utils.HtmlMessage(
            "\n".join(
                [
                    f"Package manager: {row.get('packageManager', '-')}",
                    f"Target file: {row.get('displayTargetFile', '-')}",
                    f"Project path: {row.get('path', '-')}",
                    row.get("summary", ""),
                ]
            )
        )
        message.title = f'{row.get("summary", "Snyk test")} in {row.get("displayTargetFile", "-")}.'
        _LOGGER.info(message)

        if "error" in row:
            _LOGGER.error(row["error"])
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
                ]
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
            message = module_utils.HtmlMessage(
                "<br>\n".join(
                    [
                        f'<a href="https://security.snyk.io/vuln/{vuln["id"]}">{vuln["title"]}</a>',
                        " > ".join([row.get("displayTargetFile", "-"), *vuln["from"]]),
                        *[", ".join(identifiers) for identifiers in vuln.get("identifiers", {}).values()],
                        # *[f'<a href="{reference['url']}>{reference["title"]}</a>' for reference in vuln["references"]],
                        # "",
                        # markdown.markdown(vuln["description"]),
                    ]
                ),
                title,
            )
            _LOGGER.warning(message)
            result.append(message)
    return high_vulnerabilities, fixable_vulnerabilities, fixable_vulnerabilities_summary, fixable_files_npm


async def _snyk_fix(
    branch: str,
    config: configuration.SnykConfiguration,
    local_config: configuration.SnykConfiguration,
    logs_url: str,
    result: list[module_utils.Message],
    env_no_debug: dict[str, str],
    fixable_vulnerabilities: dict[str, int],
    fixable_vulnerabilities_summary: dict[str, str],
) -> tuple[bool, module_utils.Message | None]:
    snyk_fix_success = True
    snyk_fix_message = None
    if fixable_vulnerabilities:
        command = [
            "snyk",
            "fix",
            *local_config.get(
                "fix-arguments", config.get("fix-arguments", configuration.SNYK_FIX_ARGUMENTS_DEFAULT)
            ),
        ]
        fix_message, snyk_fix_success = await _run_timeout(
            command,
            env_no_debug,
            int(os.environ.get("GHCI_SNYK_TIMEOUT", "300")),
            "Snyk fix",
            "Error while fixing the project",
            "Timeout while fixing the project",
            result,
        )
        if fix_message:
            snyk_fix_message = module_utils.AnsiMessage(fix_message.strip())
        if not snyk_fix_success:
            message = module_utils.HtmlMessage(
                "<br>\n".join(
                    [
                        *fixable_vulnerabilities_summary.values(),
                        f"{os.path.basename(os.getcwd())}:{branch}",
                        f"See logs: {logs_url}",
                    ]
                )
            )
            message.title = f"Unable to fix {len(fixable_vulnerabilities)} vulnerabilities"
            _LOGGER.error(message)
    return snyk_fix_success, snyk_fix_message


async def _npm_audit_fix(
    fixable_files_npm: dict[str, set[str]], result: list[module_utils.Message]
) -> tuple[str, bool]:
    messages: set[str] = set()
    fix_success = True
    for package_lock_file_name, file_messages in fixable_files_npm.items():
        directory = os.path.dirname(os.path.abspath(package_lock_file_name))
        messages.update(file_messages)
        command = ["npm", "audit", "fix", "--force"]
        _, success = await _run_timeout(
            command,
            os.environ.copy(),
            int(os.environ.get("GHCI_SNYK_TIMEOUT", "300")),
            "Npm audit fix",
            "Error while fixing the project",
            "Timeout while fixing the project",
            result,
            directory,
        )
        # Remove the add '~' in the version in the package.json
        with open(os.path.join(directory, "package.json"), encoding="utf-8") as package_file:
            package_json = json.load(package_file)
            for dependencies_type in ("dependencies", "devDependencies"):
                for package, version in package_json.get(dependencies_type, {}).items():
                    print(dependencies_type, package, version)
                    if version.startswith("^"):
                        package_json[dependencies_type][package] = version[1:]
            with open(os.path.join(directory, "package.json"), "w", encoding="utf-8") as package_file:
                json.dump(package_json, package_file, indent=2)

        fix_success &= success
    return "\n".join(messages), fix_success


def outdated_versions(
    security: c2cciutils.security.Security,
) -> list[str | models.OutputData]:
    """
    Check that the versions from the SECURITY.md are not outdated.
    """
    version_index = security.headers.index("Version")
    date_index = security.headers.index("Supported Until")

    errors: list[str | models.OutputData] = []

    for row in security.data:
        str_date = row[date_index]
        if str_date not in ("Unsupported", "Best effort", "To be defined"):
            date = datetime.datetime.strptime(row[date_index], "%d/%m/%Y")
            if date < datetime.datetime.now():
                errors.append(
                    f"The version '{row[version_index]}' is outdated, it can be set to "
                    "'Unsupported', 'Best effort' or 'To be defined'",
                )
    return errors


_GENERATION_TIME = None
_SOURCES = {}
_PACKAGE_VERSION: dict[str, debian_inspector.version.Version] = {}


def _get_sources(
    dist: str, config: configuration.DpkgConfiguration, local_config: configuration.DpkgConfiguration
) -> apt_repo.APTSources:
    """
    Get the sources for the distribution.
    """
    if dist not in _SOURCES:
        conf = local_config.get("sources", config.get("sources", configuration.DPKG_SOURCES_DEFAULT))
        if dist not in conf:
            raise ValueError(f"The distribution {dist} is not in the configuration")
        _SOURCES[dist] = apt_repo.APTSources(
            [
                apt_repo.APTRepository(
                    source["url"],
                    source["distribution"],
                    source["components"],
                )
                for source in conf[dist]
            ]
        )
        try:
            for package in _SOURCES[dist].packages:
                name = f"{dist}/{package.package}"
                try:
                    version = debian_inspector.version.Version.from_string(package.version)
                    if name not in _PACKAGE_VERSION:
                        _PACKAGE_VERSION[name] = version
                    elif version > _PACKAGE_VERSION[name]:
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
            _LOGGER.error("Error while loading the distribution %s: %s", dist, exception)

    return _SOURCES[dist]


async def _get_packages_version(
    package: str, config: configuration.DpkgConfiguration, local_config: configuration.DpkgConfiguration
) -> str | None:
    """Get the version of the package."""
    global _GENERATION_TIME  # pylint: disable=global-statement
    if _GENERATION_TIME is None or _GENERATION_TIME < datetime.datetime.now() - utils.parse_duration(
        os.environ.get("GHCI_DPKG_CACHE_DURATION", "3h")
    ):
        _PACKAGE_VERSION.clear()
        _SOURCES.clear()
        _GENERATION_TIME = datetime.datetime.now()
    if package not in _PACKAGE_VERSION:
        dist = package.split("/")[0]
        await asyncio.to_thread(_get_sources, dist, config, local_config)
    if package not in _PACKAGE_VERSION:
        _LOGGER.warning("No version found for %s", package)
    return str(_PACKAGE_VERSION.get(package))


async def dpkg(
    config: configuration.DpkgConfiguration, local_config: configuration.DpkgConfiguration
) -> None:
    """Update the version of packages in the file ci/dpkg-versions.yaml."""
    if not os.path.exists("ci/dpkg-versions.yaml"):
        _LOGGER.warning("The file ci/dpkg-versions.yaml does not exist")

    with open("ci/dpkg-versions.yaml", encoding="utf-8") as versions_file:
        versions_config = yaml.load(versions_file, Loader=yaml.SafeLoader)
        for versions in versions_config.values():
            for package_full in versions.keys():
                version = await _get_packages_version(package_full, config, local_config)
                if version is None:
                    _LOGGER.warning("No version found for %s", package_full)
                    continue
                if versions[package_full] is None or versions[package_full] == "None":
                    versions[package_full] = version
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

    with open("ci/dpkg-versions.yaml", "w", encoding="utf-8") as versions_file:
        yaml.dump(versions_config, versions_file, Dumper=yaml.SafeDumper)
