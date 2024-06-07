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
import markdown
import yaml  # nosec

from github_app_geo_project import models, utils
from github_app_geo_project.module import utils as module_utils
from github_app_geo_project.module.audit import configuration

_LOGGER = logging.getLogger(__name__)


async def snyk(
    branch: str, config: configuration.SnykConfiguration, local_config: configuration.SnykConfiguration
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
    result = []

    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        ["echo"],
        capture_output=True,
        encoding="utf-8",
        timeout=30,
    )
    message = module_utils.ansi_proc_message(proc)
    message.title = "Environment variables"
    _LOGGER.debug(message)

    env = os.environ.copy()
    env["PATH"] = f'{env["HOME"]}/.local/bin:{env["PATH"]}'
    _LOGGER.debug("Updated path: %s", env["PATH"])

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
            async with asyncio.timeout(1200):
                try:
                    command = [
                        "pip",
                        "install",
                        *local_config.get("pip-install-arguments", config.get("pip-install-arguments", [])),
                        f"--requirement={file}",
                    ]
                    async_proc = await asyncio.create_subprocess_exec(
                        *command, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                    )
                    stdout, stderr = await async_proc.communicate()
                    assert async_proc.returncode is not None
                    message = module_utils.AnsiProcessMessage(
                        command, async_proc.returncode, stdout.decode(), stderr.decode()
                    )
                except FileNotFoundError as exception:
                    _LOGGER.exception("Pip not found: %s", exception)
                    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                        ["find", "/", "-name", "pip"],
                        capture_output=True,
                        encoding="utf-8",
                        timeout=30,
                    )
                    message = module_utils.ansi_proc_message(proc)
                    message.title = "Find pip"
                    _LOGGER.debug(message)
            if async_proc.returncode != 0:
                message.title = f"Error while installing the dependencies from {file}"
                _LOGGER.warning(message)
                result.append(message)
            message.title = f"Dependencies installed from {file}"
            _LOGGER.debug(message)

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

            async with asyncio.timeout(300):
                try:
                    command = [
                        "pipenv",
                        "install",
                        *local_config.get("pipenv-sync-arguments", config.get("pipenv-sync-arguments", [])),
                    ]
                    async_proc = await asyncio.create_subprocess_exec(
                        *command,
                        cwd=directory,
                        env=env,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await async_proc.communicate()
                    assert async_proc.returncode is not None
                    message = module_utils.AnsiProcessMessage(
                        command, async_proc.returncode, stdout.decode(), stderr.decode()
                    )
                except FileNotFoundError as exception:
                    _LOGGER.exception("Pipenv not found: %s", exception)
                    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                        ["find", "/", "-name", "pipenv"],
                        capture_output=True,
                        encoding="utf-8",
                        timeout=30,
                    )
                    message = module_utils.ansi_proc_message(proc)
                    message.title = "Find pipenv"
                    _LOGGER.debug(message)
            if async_proc.returncode != 0:
                message.title = f"Error while installing the dependencies from {file}"
                _LOGGER.warning(message)
                result.append(message)
            else:
                message.title = f"Dependencies installed from {file}"
                _LOGGER.debug(message)

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
            async with asyncio.timeout(1200):
                try:
                    command = ["poetry", "install"]
                    async_proc = await asyncio.create_subprocess_exec(
                        *command,
                        cwd=os.path.dirname(os.path.abspath(file)),
                        env=env,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    stdout, stderr = await async_proc.communicate()
                    assert async_proc.returncode is not None
                    message = module_utils.AnsiProcessMessage(
                        command, async_proc.returncode, stdout.decode(), stderr.decode()
                    )
                    if async_proc.returncode != 0:
                        message.title = f"Error while installing the dependencies from {file}"
                        _LOGGER.warning(message)
                        result.append(message)
                except FileNotFoundError as exception:
                    _LOGGER.exception("Poetry not found: %s", exception)
                    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                        ["find", "/", "-name", "poetry"],
                        capture_output=True,
                        encoding="utf-8",
                        timeout=30,
                    )
                    message = module_utils.ansi_proc_message(proc)
                    message.title = "Find poetry"
                    _LOGGER.debug(message)

            message.title = f"Dependencies installed from {file}"
            _LOGGER.debug(message)

    env = {**os.environ}
    env["FORCE_COLOR"] = "true"
    env_no_debug = {**env}
    env["DEBUG"] = "*snyk*"  # debug mode

    command = ["snyk", "monitor", f"--target-reference={branch}"] + config.get(
        "monitor-arguments", configuration.SNYK_MONITOR_ARGUMENTS_DEFAULT
    )
    async with asyncio.timeout(300):
        async_proc = await asyncio.create_subprocess_exec(
            *command, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await async_proc.communicate()
        assert async_proc.returncode is not None
        message = module_utils.AnsiProcessMessage(
            command, async_proc.returncode, stdout.decode(), stderr.decode()
        )
    if async_proc.returncode != 0:
        message.title = "Error while monitoring the project"
        _LOGGER.warning(message)
        result.append(message)
    else:
        message.title = "Project monitored"
        _LOGGER.debug(message)

    command = ["snyk", "test", "--json"] + config.get(
        "test-arguments", configuration.SNYK_TEST_ARGUMENTS_DEFAULT
    )
    async with asyncio.timeout(300):
        test_proc = await asyncio.create_subprocess_exec(
            *command, env=env_no_debug, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await test_proc.communicate()

    test_json_str = stdout.decode()
    message = module_utils.HtmlMessage(utils.format_json_str(test_json_str))
    message.title = "Snyk test JSON output"
    if test_json_str:
        _LOGGER.debug(message)
    else:
        _LOGGER.error("Snyk test JSON returned nothing on project %s branch %s", os.getcwd(), branch)

    test_json = json.loads(test_json_str) if test_json_str else []

    if not isinstance(test_json, list):
        test_json = [test_json]

    high_vulnerabilities: dict[str, int] = {}
    fixable_vulnerabilities: dict[str, int] = {}
    for row in test_json:
        message = module_utils.HtmlMessage(
            "<br>\n".join(
                [
                    f"Package manager: {row.get('packageManager', '-')}",
                    f"Target file: {row.get('displayTargetFile', '-')}",
                    f"Project path: {row.get('path', '-')}",
                    row.get("summary", ""),
                ]
            )
        )
        message.title = row.get("summary", "Snyk test")
        _LOGGER.info(message)

        if "error" in row:
            _LOGGER.error(row["error"])
        for vuln in row.get("vulnerabilities", []):
            fixable = vuln.get("isUpgradable", False) or vuln.get("isPatchable", False)
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
                    f'<a href="https://security.snyk.io/vuln/{vuln["id"]}">{vuln["title"]}</a>',
                ]
            )
            if vuln.get("isUpgradable", False):
                title += " [Fixed in: " + ", ".join(vuln["fixedIn"]) + "]."
            elif vuln.get("isPatchable", False):
                title += " [Patch available]."
            else:
                title += "."
            message = module_utils.HtmlMessage(
                "\n".join(
                    [
                        vuln["id"],
                        " > ".join(vuln["from"]),
                        *[
                            f"{identifier_type} {', '.join(identifiers)}"
                            for identifier_type, identifiers in vuln["identifiers"].items()
                        ],
                        *[f"[{reference['title']}]({reference['url']})" for reference in vuln["references"]],
                        "",
                        markdown.markdown(vuln["description"]),
                    ]
                ),
                title,
            )
            _LOGGER.warning(message)
            result.append(message)

    snyk_fix_success = True
    snyk_fix_message = None
    if fixable_vulnerabilities:
        command = ["snyk", "fix"] + config.get("fix-arguments", configuration.SNYK_FIX_ARGUMENTS_DEFAULT)
        async with asyncio.timeout(300):
            snyk_fix_proc = await asyncio.create_subprocess_exec(
                *command, env=env_no_debug, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await snyk_fix_proc.communicate()
            assert snyk_fix_proc.returncode is not None
            snyk_fix_message = module_utils.AnsiMessage(stdout.decode().strip())
            message = module_utils.AnsiProcessMessage(
                command, snyk_fix_proc.returncode, stdout.decode(), stderr.decode()
            )
        snyk_fix_success = snyk_fix_proc.returncode == 0
        if snyk_fix_proc.returncode != 0:
            message.title = "Error while fixing the project"
            _LOGGER.error(message)
            result.append(message)
        else:
            message.title = "Snyk fix applied"
            _LOGGER.debug(message)

    return_message = [
        *[f"{number} {severity} vulnerabilities" for severity, number in high_vulnerabilities.items()],
        *[
            f"{number} {severity} vulnerabilities can be fixed"
            for severity, number in fixable_vulnerabilities.items()
        ],
        *([] if snyk_fix_success else ["Error while fixing the vulnerabilities"]),
    ]

    return result, snyk_fix_message, return_message, snyk_fix_success


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
        for package in _SOURCES[dist].packages:
            name = f"{dist}/{package.package}"
            version = debian_inspector.version.Version.from_string(package.version)
            if name not in _PACKAGE_VERSION:
                _PACKAGE_VERSION[name] = version
            elif version > _PACKAGE_VERSION[name]:
                _PACKAGE_VERSION[name] = version

    return _SOURCES[dist]


async def _get_packages_version(
    package: str, config: configuration.DpkgConfiguration, local_config: configuration.DpkgConfiguration
) -> str | None:
    """Get the version of the package."""
    if _GENERATION_TIME is None or _GENERATION_TIME < datetime.datetime.now() - utils.parse_duration(
        os.environ.get("GHCI_DPKG_CACHE_DURATION", "3h")
    ):
        _PACKAGE_VERSION.clear()
        _SOURCES.clear()
        datetime.datetime.now()
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
                if version:
                    versions[package_full] = version

    with open("ci/dpkg-versions.yaml", "w", encoding="utf-8") as versions_file:
        yaml.dump(versions_config, versions_file, Dumper=yaml.SafeDumper)
