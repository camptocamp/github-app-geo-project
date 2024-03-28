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
import yaml  # nosec
from ansi2html import Ansi2HTMLConverter

from github_app_geo_project.module import utils
from github_app_geo_project.module.audit import configuration

_LOGGING = logging.getLogger(__name__)


def snyk(
    branch: str, config: configuration.AuditConfiguration, local_config: configuration.AuditConfiguration
) -> tuple[list[str], str, bool]:
    """
    Audit the code with Snyk.
    """
    install_success = True
    result = []
    create_issue = True

    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        ["git", "ls-files", "requirements.txt", "*/requirements.txt"], capture_output=True, encoding="utf-8"
    )
    if proc.returncode != 0:
        message = utils.ansi_proc_dashboard(proc)
        _LOGGING.error(message)
        result.append(f"<details>\n<summary>Error in ls-files</summary>\n{message}\n</details>")
    else:
        for file in proc.stdout.strip().split("\n"):
            if not file:
                continue
            if file in local_config.get("files-no-install", []):
                continue
            proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                [
                    "pip",
                    "install",
                    f"--requirement={file}",
                    *local_config.get("pip-install-arguments", []),
                ],
                capture_output=True,
                encoding="utf-8",
            )
            if proc.returncode != 0:
                message = utils.ansi_proc_dashboard(proc)
                _LOGGING.error(message)
                result.append(
                    f"<details>\n<summary>Error while installing the dependencies from {file}</summary>\n{message}\n</details>"
                )
                continue

    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        ["git", "ls-files", "Pipfile", "*/Pipfile"], capture_output=True, encoding="utf-8"
    )
    if proc.returncode != 0:
        message = utils.ansi_proc_dashboard(proc)
        _LOGGING.error(message)
        result.append(f"<details>\n<summary>Error in ls-files</summary>\n{message}\n</details>")
    else:
        for file in proc.stdout.strip().split("\n"):
            if not file:
                continue
            if file in local_config.get("files-no-install", []):
                continue
            directory = os.path.dirname(os.path.abspath(file))

            proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                [
                    "pipenv",
                    "sync",
                    *local_config.get("pipenv-sync-arguments", []),
                ],
                cwd=directory,
                capture_output=True,
                encoding="utf-8",
            )
            if proc.returncode != 0:
                message = utils.ansi_proc_dashboard(proc)
                _LOGGING.error(message)
                result.append(
                    f"<details>\n<summary>Error while installing the dependencies from {file}</summary>\n{message}\n</details>"
                )
            install_success &= proc.returncode == 0

    env = {**os.environ}
    env["FORCE_COLOR"] = "true"
    ansi_converter = Ansi2HTMLConverter(inline=True)

    command = ["snyk", "monitor", f"--target-reference={branch}"] + config.get(
        "monitor-arguments", c2cciutils.configuration.AUDIT_SNYK_MONITOR_ARGUMENTS_DEFAULT
    )
    proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        command, env=env, capture_output=True, encoding="utf-8"
    )
    if proc.returncode != 0:
        message = utils.ansi_proc_dashboard(proc)
        _LOGGING.error(message)
        result.append(
            f"<details>\n<summary>Error while monitoring the project</summary>\n{message}\n</details>"
        )

    command = ["snyk", "test", "--json"] + config.get(
        "test-arguments", c2cciutils.configuration.AUDIT_SNYK_TEST_ARGUMENTS_DEFAULT
    )
    test_proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        command, env=env, capture_output=True, encoding="utf-8"
    )
    test_json = json.loads(test_proc.stdout)
    for raw in test_json:
        result.append(f"{raw['targetFile']} ({raw['packageManager']})")
        for vuln in raw["vulnerabilities"]:
            result += [
                "<details>",
                "<summary>",
                f"[{vuln['severity'].upper()}] {vuln['packageName']}@{vuln['version']}: {vuln['title']}, fixed in {', '.join(vuln['fixedIn'])}",
                "</summary>",
                vuln["id"],
                " > ".join(vuln["from"]),
                *[
                    f"{identifier_type} {', '.join(identifiers)}"
                    for identifier_type, identifiers in vuln["identifiers"].items()
                ],
                *[f"[{reference['title']}]({reference['url']})" for reference in vuln["references"]],
                "",
                vuln["description"],
                "",
                "</details>",
            ]

    command = ["snyk", "fix"] + config.get(
        "fix-arguments", c2cciutils.configuration.AUDIT_SNYK_FIX_ARGUMENTS_DEFAULT
    )
    snyk_fix_proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
        command, env=env, capture_output=True, encoding="utf-8"
    )
    snyk_fix_message = ansi_converter.convert(snyk_fix_proc.stdout.strip())
    if snyk_fix_proc.returncode == 0:
        create_issue = False

    return result, snyk_fix_message, create_issue


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


def dpkg() -> list[str]:
    """Update the version of packages in the file ci/dpkg-versions.yaml."""
    if not os.path.exists("ci/dpkg-versions.yaml"):
        return ["The file ci/dpkg-versions.yaml does not exist"]

    cache: dict[str, dict[str, str]] = {}
    results: list[str] = []
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
                            message = utils.ansi_proc_dashboard(proc)
                            _LOGGING.error(message)
                            results.append(
                                f"<details>\n<summary>Error while removing the container</summary>\n{message}\n</details>"
                            )
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
                            message = utils.ansi_proc_dashboard(proc)
                            _LOGGING.error(message)
                            results.append(
                                f"<details>\n<summary>Error while creating the container</summary>\n{message}\n</details>"
                            )

                        proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                            ["docker", "exec", "apt", "apt-get", "update"],
                            capture_output=True,
                            encoding="utf-8",
                        )
                        if proc.returncode != 0:
                            message = utils.ansi_proc_dashboard(proc)
                            _LOGGING.error(message)
                            results.append(
                                f"<details>\n<summary>Error while updating the container</summary>\n{message}\n</details>"
                            )

                        package_re = re.compile(r"^([^ /]+)/[a-z-,]+ ([^ ]+) (all|amd64)( .*)?$")
                        proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                            ["docker", "exec", "apt", "apt", "list"],
                            capture_output=True,
                            encoding="utf-8",
                        )
                        if proc.returncode != 0:
                            message = utils.ansi_proc_dashboard(proc)
                            _LOGGING.error(message)
                            results.append(
                                f"<details>\n<summary>Error while listing the packages</summary>\n{message}\n</details>"
                            )
                            return results
                        for proc_line in proc.stdout.split("\n"):
                            package_match = package_re.match(proc_line)
                            if package_match is None:
                                print(f"not matching: {proc_line}")
                                continue
                            cache.setdefault(dist, {})[package_match.group(1)] = package_match.group(2)

                        proc = subprocess.run(  # nosec # pylint: disable=subprocess-run-check
                            ["docker", "rm", "--force", "apt"],
                            capture_output=True,
                            encoding="utf-8",
                        )
                        if proc.returncode != 0:
                            message = utils.ansi_proc_dashboard(proc)
                            _LOGGING.error(message)
                            results.append(
                                f"<details>\n<summary>Error while removing the container</summary>\n{message}\n</details>"
                            )
                if package in cache[dist]:
                    versions[package_full] = cache[dist][package]

    with open("ci/dpkg-versions.yaml", "w", encoding="utf-8") as versions_file:
        yaml.dump(versions_config, versions_file, Dumper=yaml.SafeDumper)

    return results
