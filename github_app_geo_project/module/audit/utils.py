"""
The auditing functions.
"""

import datetime
import json
import logging
import os.path
import re
import subprocess  # nosec

import c2cciutils.security
import pygments.formatters
import pygments.lexers
import yaml  # nosec

from github_app_geo_project.module import utils
from github_app_geo_project.module.audit import configuration

_LOGGING = logging.getLogger(__name__)


def snyk(
    branch: str, config: configuration.AuditConfiguration, local_config: configuration.AuditConfiguration
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
        _LOGGING.warning(message.to_html())
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
                _LOGGING.warning(message.to_html())
                result.append(message)
            message.title = f"Dependencies installed from {file}"
            _LOGGING.debug(message.to_html())

    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        ["git", "ls-files", "Pipfile", "*/Pipfile"], capture_output=True, encoding="utf-8"
    )
    if proc.returncode != 0:
        message = utils.ansi_proc_message(proc)
        message.title = "Error in ls-files"
        _LOGGING.warning(message.to_html())
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
                _LOGGING.warning(message.to_html())
                result.append(message)
            else:
                message.title = f"Dependencies installed from {file}"
                _LOGGING.debug(message.to_html())

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
        _LOGGING.warning(message.to_html())
        result.append(message)
    else:
        message.title = "Project monitored"
        _LOGGING.debug(message.to_html())

    command = ["snyk", "test", "--json"] + config.get(
        "test-arguments", configuration.SNYK_TEST_ARGUMENTS_DEFAULT
    )
    test_proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        command, env=env, capture_output=True, encoding="utf-8"
    )
    lexer = pygments.lexers.JsonLexer()
    formatter = pygments.formatters.HtmlFormatter(noclasses=True, style="github-dark")

    test_json = json.loads(test_proc.stdout)
    _LOGGING.debug(
        "Snyk test output:\n%s", pygments.highlight(json.dumps(test_json, indent=4), lexer, formatter)
    )

    if isinstance(test_json, dict) and test_json.get("ok", True) is False:
        _LOGGING.warning(test_json.get("error"))

        command = ["snyk", "test"] + config.get("test-arguments", configuration.SNYK_TEST_ARGUMENTS_DEFAULT)
        test_proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
            command, env=env, capture_output=True, encoding="utf-8"
        )
        dashboard_message = utils.ansi_proc_message(proc)
        dashboard_message.title = "Error while testing the project"
        _LOGGING.warning(dashboard_message.to_html())
    else:
        for raw in test_json:
            result.append(
                utils.HtmlMessage(f"{raw.get('targetFile', '-')} ({raw.get('packageManager', '-')})")
            )
            for vuln in raw["vulnerabilities"]:
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

    command = ["snyk", "fix"] + config.get("fix-arguments", configuration.SNYK_FIX_ARGUMENTS_DEFAULT)
    snyk_fix_proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        command, env=env, capture_output=True, encoding="utf-8"
    )
    snyk_fix_message = utils.AnsiMessage(snyk_fix_proc.stdout.strip())
    if snyk_fix_proc.returncode != 0:
        # Hide error if there is no error in test
        if result:
            result.append(utils.ansi_proc_message(snyk_fix_proc))
        _LOGGING.error("Snyk fix error:\n%s", snyk_fix_message.to_html())
    else:
        _LOGGING.info("Snyk fix:\n%s", snyk_fix_message.to_html())

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


def dpkg() -> list[utils.Message]:
    """Update the version of packages in the file ci/dpkg-versions.yaml."""
    if not os.path.exists("ci/dpkg-versions.yaml"):
        return [utils.HtmlMessage("The file ci/dpkg-versions.yaml does not exist")]

    cache: dict[str, dict[str, str]] = {}
    results: list[utils.Message] = []
    with open("ci/dpkg-versions.yaml", encoding="utf-8") as versions_file:
        versions_config = yaml.load(versions_file, Loader=yaml.SafeLoader)
        for versions in versions_config.values():
            for package_full in versions.keys():
                dist, package = package_full.split("/")
                if dist not in cache:
                    correspondence = {
                        "ubuntu_22_04": ("ubuntu", "22.04"),
                        "debian_11": ("debian", "11"),
                        "debian_12": ("debian", "12"),
                    }
                    if dist in correspondence:
                        images, tag = correspondence[dist]
                        proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                            ["docker", "rm", "--force", "apt"],
                            capture_output=True,
                            encoding="utf-8",
                        )
                        if proc.returncode == 0:
                            message = utils.ansi_proc_message(proc)
                            message.title = "Error while removing the container"
                            _LOGGING.warning(message.to_html())
                            results.append(message)
                        proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                            [
                                "docker",
                                "run",
                                "--tty",
                                "--interactive",
                                "--detach",
                                "--name=apt",
                                "--entrypoint=",
                                f"{images}:{tag}",
                                "tail",
                                "--follow",
                                "/dev/null",
                            ],
                            capture_output=True,
                            encoding="utf-8",
                        )
                        if proc.returncode != 0:
                            message = utils.ansi_proc_message(proc)
                            message.title = "Error while creating the container"
                            _LOGGING.warning(message.to_html())
                            results.append(message)

                        proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                            ["docker", "exec", "apt", "apt-get", "update"],
                            capture_output=True,
                            encoding="utf-8",
                        )
                        if proc.returncode != 0:
                            message = utils.ansi_proc_message(proc)
                            message.title = "Error while updating the container"
                            _LOGGING.warning(message.to_html())
                            results.append(message)

                        package_re = re.compile(r"^([^ /]+)/[a-z-,]+ ([^ ]+) (all|amd64)( .*)?$")
                        proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                            ["docker", "exec", "apt", "apt", "list"],
                            capture_output=True,
                            encoding="utf-8",
                        )
                        if proc.returncode != 0:
                            message = utils.ansi_proc_message(proc)
                            message.title = "Error while listing the packages"
                            _LOGGING.warning(message.to_html())
                            results.append(message)
                            return results
                        for proc_line in proc.stdout.split("\n"):
                            package_match = package_re.match(proc_line)
                            if package_match is None:
                                _LOGGING.debug("Not matching: %s", proc_line)
                                continue
                            cache.setdefault(dist, {})[package_match.group(1)] = package_match.group(2)

                        proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                            ["docker", "rm", "--force", "apt"],
                            capture_output=True,
                            encoding="utf-8",
                        )
                        if proc.returncode != 0:
                            message = utils.ansi_proc_message(proc)
                            message.title = "Error while removing the container"
                            _LOGGING.warning(message.to_html())
                            results.append(message)
                if package in cache[dist]:
                    versions[package_full] = cache[dist][package]

    with open("ci/dpkg-versions.yaml", "w", encoding="utf-8") as versions_file:
        yaml.dump(versions_config, versions_file, Dumper=yaml.SafeDumper)

    return results
