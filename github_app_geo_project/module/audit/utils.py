"""
The auditing functions.
"""

import datetime
import json
import logging
import os.path
import subprocess  # nosec

import apt_repo
import c2cciutils.security
import debian_inspector.version
import pygments.formatters
import pygments.lexers
import yaml  # nosec

from github_app_geo_project.module import utils
from github_app_geo_project.module.audit import configuration

_LOGGING = logging.getLogger(__name__)


def snyk(
    branch: str, config: configuration.SnykConfiguration, local_config: configuration.SnykConfiguration
) -> tuple[list[utils.Message], utils.Message]:
    """
    Audit the code with Snyk.
    """
    result = []

    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        ["git", "ls-files", "requirements.txt", "*/requirements.txt"], capture_output=True, encoding="utf-8"
    )
    if proc.returncode != 0:
        message = utils.ansi_proc_message(proc)
        message.title = "Error in ls-files"
        _LOGGING.warning(message.to_html(style="collapse"))
        result.append(message)
    else:
        for file in proc.stdout.strip().split("\n"):
            if not file:
                continue
            if file in local_config.get("files-no-install", config.get("files-no-install", [])):
                continue
            proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                [
                    "pip",
                    "install",
                    *local_config.get("pip-install-arguments", config.get("pip-install-arguments", [])),
                    f"--requirement={file}",
                ],
                capture_output=True,
                encoding="utf-8",
            )
            message = utils.ansi_proc_message(proc)
            if proc.returncode != 0:
                message.title = f"Error while installing the dependencies from {file}"
                _LOGGING.warning(message.to_html(style="collapse"))
                result.append(message)
            message.title = f"Dependencies installed from {file}"
            _LOGGING.debug(message.to_html(style="collapse"))

    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        ["git", "ls-files", "Pipfile", "*/Pipfile"], capture_output=True, encoding="utf-8"
    )
    if proc.returncode != 0:
        message = utils.ansi_proc_message(proc)
        message.title = "Error in ls-files"
        _LOGGING.warning(message.to_html(style="collapse"))
        result.append(message)
    else:
        for file in proc.stdout.strip().split("\n"):
            if not file:
                continue
            if file in local_config.get("files-no-install", config.get("files-no-install", [])):
                continue
            directory = os.path.dirname(os.path.abspath(file))

            proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                [
                    "pipenv",
                    "sync",
                    *local_config.get("pipenv-sync-arguments", config.get("pipenv-sync-arguments", [])),
                ],
                cwd=directory,
                capture_output=True,
                encoding="utf-8",
            )
            message = utils.ansi_proc_message(proc)
            if proc.returncode != 0:
                message.title = f"Error while installing the dependencies from {file}"
                _LOGGING.warning(message.to_html(style="collapse"))
                result.append(message)
            else:
                message.title = f"Dependencies installed from {file}"
                _LOGGING.debug(message.to_html(style="collapse"))

    env = {**os.environ}
    env["FORCE_COLOR"] = "true"
    env["DEBUG"] = "*snyk*"  # debug mode

    command = ["snyk", "monitor", f"--target-reference={branch}"] + config.get(
        "monitor-arguments", configuration.SNYK_MONITOR_ARGUMENTS_DEFAULT
    )
    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        command, env=env, capture_output=True, encoding="utf-8"
    )
    message = utils.ansi_proc_message(proc)
    if proc.returncode != 0:
        message.title = "Error while monitoring the project"
        _LOGGING.warning(message.to_html(style="collapse"))
        result.append(message)
    else:
        message.title = "Project monitored"
        _LOGGING.debug(message.to_html(style="collapse"))

    command = ["snyk", "test", "--json"] + config.get(
        "test-arguments", configuration.SNYK_TEST_ARGUMENTS_DEFAULT
    )
    test_proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        command, env=env, capture_output=True, encoding="utf-8"
    )
    lexer = pygments.lexers.JsonLexer()
    formatter = pygments.formatters.HtmlFormatter(noclasses=True, style="github-dark")

    test_json = json.loads(test_proc.stdout)
    message = utils.HtmlMessage(pygments.highlight(json.dumps(test_json, indent=4), lexer, formatter))
    message.title = "Snyk test output"
    _LOGGING.debug(message.to_html(style="collapse"))

    if not isinstance(test_json, list):
        test_json = [test_json]

    vulnerabilities = False
    error = False
    for row in test_json:
        if test_json.get("ok", True) is False:
            _LOGGING.warning("Error on file %s: %s", row.get("targetFile", "-"), test_json.get("error"))
            error = True
            continue
        for row in test_json:
            if not row.get("vulnerabilities", []):
                continue

            result.append(
                utils.HtmlMessage(f"{row.get('targetFile', '-')} ({row.get('packageManager', '-')})")
            )
            for vuln in row["vulnerabilities"]:
                if vuln.get("isUpgradable", False) or vuln.get("isPatchable", False):
                    vulnerabilities = True
                result.append(
                    utils.HtmlMessage(
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
        test_proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
            command, env=env, capture_output=True, encoding="utf-8"
        )
        dashboard_message = utils.ansi_proc_message(proc)
        dashboard_message.title = "Error while testing the project"
        _LOGGING.error(dashboard_message.to_html(style="collapse"))

    if vulnerabilities:
        message = utils.HtmlMessage(" ".join(m.to_html() for m in result))
        message.title = "Vulnerabilities found"
        _LOGGING.warning(message.to_html(style="collapse"))

    command = ["snyk", "fix"] + config.get("fix-arguments", configuration.SNYK_FIX_ARGUMENTS_DEFAULT)
    snyk_fix_proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        command, env=env, capture_output=True, encoding="utf-8"
    )
    snyk_fix_message = utils.AnsiMessage(snyk_fix_proc.stdout.strip())
    message = utils.ansi_proc_message(snyk_fix_proc)
    if snyk_fix_proc.returncode != 0:
        message.title = "Error while fixing the project"
        _LOGGING.warning(message.to_html(style="collapse"))
    else:
        message.title = "Snyk fix applied"
        _LOGGING.debug(message.to_html(style="collapse"))

    if snyk_fix_proc.returncode != 0 and vulnerabilities:
        result.append(utils.ansi_proc_message(snyk_fix_proc))

    return result, snyk_fix_message


def outdated_versions(
    security: c2cciutils.security.Security,
) -> list[str]:
    """
    Check that the versions from the SECURITY.md are not outdated.
    """
    version_index = security.headers.index("Version")
    date_index = security.headers.index("Supported Until")

    errors = []

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


def _get_packages_version(
    package: str, config: configuration.DpkgConfiguration, local_config: configuration.DpkgConfiguration
) -> str | None:
    """Get the version of the package."""
    if package not in _PACKAGE_VERSION:
        dist, pkg = package.split("/")
        sources = _get_sources(dist, config, local_config)
        versions = sorted(
            [
                debian_inspector.version.Version.from_string(package.version)
                for package in sources.get_packages_by_name(pkg)
            ]
        )
        if not versions:
            _LOGGING.warning("No version found for %s", package)
            return None
        else:
            _PACKAGE_VERSION[package] = str(versions[-1])
    return _PACKAGE_VERSION[package]


def dpkg(config: configuration.DpkgConfiguration, local_config: configuration.DpkgConfiguration) -> None:
    """Update the version of packages in the file ci/dpkg-versions.yaml."""
    if not os.path.exists("ci/dpkg-versions.yaml"):
        _LOGGING.error("The file ci/dpkg-versions.yaml does not exist")

    with open("ci/dpkg-versions.yaml", encoding="utf-8") as versions_file:
        versions_config = yaml.load(versions_file, Loader=yaml.SafeLoader)
        for versions in versions_config.values():
            for package_full in versions.keys():
                version = _get_packages_version(package_full, config, local_config)
                if version:
                    versions[package_full] = version

    with open("ci/dpkg-versions.yaml", "w", encoding="utf-8") as versions_file:
        yaml.dump(versions_config, versions_file, Dumper=yaml.SafeDumper)
