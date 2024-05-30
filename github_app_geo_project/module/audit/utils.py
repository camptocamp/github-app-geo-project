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


async def snyk(
    branch: str, config: configuration.SnykConfiguration, local_config: configuration.SnykConfiguration
) -> tuple[list[module_utils.Message], module_utils.Message]:
    """
    Audit the code with Snyk.
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
    message.title = "Snyk test output"
    if test_json_str:
        _LOGGER.debug(message)
    else:
        _LOGGER.error("Snyk test returned nothing on project %s branch %s", os.getcwd(), branch)
    test_json = json.loads(test_json_str) if test_json_str else []

    if not isinstance(test_json, list):
        test_json = [test_json]

    vulnerabilities = False
    error = False
    for row in test_json:
        if row.get("ok", True) is False:
            _LOGGER.warning("Error on file %s: %s", row.get("targetFile", "-"), row.get("error"))
            error = True
            continue

        else:
            if not row.get("vulnerabilities", []):
                continue

            result.append(
                module_utils.HtmlMessage(
                    f"<p>Package manager: {row.get('packageManager', '-')}</p>"
                    f"<p>Target file: {row.get('targetFile', '-')}</p>"
                    f"<p>Project path: {row.get('projectPath', '-')}</p>"
                )
            )

            # TODO: Example message:
            # Pin idna@3.3 to idna@3.7 to fix
            # ✗ Resource Exhaustion (new) [Medium Severity][https://security.snyk.io/vuln/SNYK-PYTHON-IDNA-6597975] in idna@3.3
            #   introduced by requests@2.31.0 > idna@3.3 and 6 other path(s)

            for vuln in row["vulnerabilities"]:
                if vuln.get("isUpgradable", False) or vuln.get("isPatchable", False):
                    vulnerabilities = True
                result.append(
                    module_utils.HtmlMessage(
                        "\n".join(
                            [
                                vuln["id"],
                                " > ".join(vuln["from"]),
                                *[
                                    f"{identifier_type} {', '.join(identifiers)}"
                                    for identifier_type, identifiers in vuln["identifiers"].items()
                                ],
                                *[
                                    f"[{reference['title']}]({reference['url']})"
                                    for reference in vuln["references"]
                                ],
                                "",
                                vuln["description"],
                            ]
                        ),
                        f"[{vuln['severity'].upper()}] {vuln['packageName']}@{vuln['version']}: {vuln['title']}, fixed in {', '.join(vuln['fixedIn'])}",
                    )
                )

    if error:
        command = ["snyk", "test"] + config.get("test-arguments", configuration.SNYK_TEST_ARGUMENTS_DEFAULT)
        async with asyncio.timeout(300):
            test_proc = await asyncio.create_subprocess_exec(
                *command, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await test_proc.communicate()
            assert test_proc.returncode is not None
            dashboard_message = module_utils.AnsiProcessMessage(
                command, test_proc.returncode, stdout.decode(), stderr.decode()
            )
        dashboard_message.title = "Error while testing the project"
        _LOGGER.error(dashboard_message)

    if vulnerabilities:
        message = module_utils.HtmlMessage(" ".join(m.to_html() for m in result))
        message.title = "Vulnerabilities found"
        _LOGGER.warning(message)

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
    if snyk_fix_proc.returncode != 0:
        message.title = "Error while fixing the project"
        _LOGGER.warning(message)
        result.append(message)
    else:
        message.title = "Snyk fix applied"
        _LOGGER.debug(message)

    if snyk_fix_proc.returncode != 0 and vulnerabilities:
        result.append(
            module_utils.AnsiProcessMessage(
                command, snyk_fix_proc.returncode, stdout.decode(), stderr.decode()
            )
        )

    return result, snyk_fix_message


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


_SOURCES = {}


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

    return _SOURCES[dist]


_PACKAGE_VERSION: dict[str, str] = {}


def _fill_versions(
    package: str, config: configuration.DpkgConfiguration, local_config: configuration.DpkgConfiguration
) -> None:
    if package not in _PACKAGE_VERSION:
        dist, pkg = package.split("/")
        if pkg is None:
            _LOGGER.warning("No package found in %s", package)
            return
        sources = _get_sources(dist, config, local_config)
        versions = sorted(
            [
                debian_inspector.version.Version.from_string(apt_package.version)
                for apt_package in sources.get_packages_by_name(package)
                if apt_package.version
            ]
        )
        if not versions:
            _LOGGER.warning("No version found for %s", package)
        else:
            _PACKAGE_VERSION[package] = str(versions[-1])


async def _get_packages_version(
    package: str, config: configuration.DpkgConfiguration, local_config: configuration.DpkgConfiguration
) -> str | None:
    """Get the version of the package."""
    if package not in _PACKAGE_VERSION:
        await asyncio.to_thread(_fill_versions, package, config, local_config)
    return _PACKAGE_VERSION.get(package)


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
