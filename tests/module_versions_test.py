import os
from unittest.mock import Mock

import pytest
import responses

from github_app_geo_project.module.versions import (
    ProcessOutput,
    Versions,
    _canonical_minor_version,
    _Dependencies,
    _DependenciesBranches,
    _Dependency,
    _EventData,
    _read_dependencies,
    _TransversalStatus,
    _TransversalStatusNameByDatasource,
    _TransversalStatusNameInDatasource,
    _TransversalStatusRepo,
    _TransversalStatusVersion,
    _TransversalStatusVersions,
    _update_upstream_versions,
)


def test_get_actions() -> None:
    versions = Versions()
    context = Mock()
    context.event_data = {"type": "event", "name": "daily"}
    actions = versions.get_actions(context)
    assert len(actions) == 1
    assert actions[0].data == _EventData(step=1)


@pytest.mark.asyncio
async def test_process_step_2() -> None:
    versions = Versions()
    context = Mock()
    context.module_event_data = _EventData(step=2, branch="master")
    context.transversal_status = _TransversalStatus()
    context.github_project = Mock()
    context.github_project.owner = "camptocamp"
    context.github_project.repository = "test"
    os.environ["TEST"] = "TRUE"
    os.environ[
        "RENOVATE_GRAPH"
    ] = """WARN: GitHub token is required for some dependencies
       "githubDeps": [
         "camptocamp/backport-action",
         "actions/checkout",
         "github/codeql-action",
         "actions/cache",
         "actions/upload-artifact",
         "actions/github-script",
         "python"
       ]
 INFO: Extracted dependencies
       "packageFiles": {
         "docker-compose": [
           {
             "deps": [
               {
                 "depName": "sbrunner/scan-to-paperless",
                 "replaceString": "sbrunner/scan-to-paperless",
                 "autoReplaceStringTemplate": "{{depName}}{{#if newValue}}:{{newValue}}{{/if}}{{#if newDigest}}@{{newDigest}}{{/if}}",
                 "datasource": "docker"
               }
             ],
             "packageFile": "docker-compose.yaml"
           }
         ]
    }
"""
    output = await versions.process(context)
    assert isinstance(output.transversal_status, _TransversalStatus)
    transversal_status_json = versions.transversal_status_to_json(output.transversal_status)
    assert transversal_status_json.get("repositories") == {
        "camptocamp/test": {
            "versions": {
                "master": {
                    "dependencies_by_datasource": {},
                    "names_by_datasource": {
                        "docker": {
                            "names": [
                                "camptocamp/github-app-geo-project:master",
                            ],
                        },
                        "github": {
                            "names": [
                                "camptocamp/test",
                            ],
                        },
                        "npm": {
                            "names": [
                                "ghci",
                            ],
                        },
                        "pypi": {
                            "names": [
                                "github-app-geo-project",
                            ],
                        },
                    },
                    "support": "Best Effort",
                },
            },
        },
    }


def test_get_transversal_dashboard() -> None:
    versions = Versions()
    context = Mock()
    context.status = _TransversalStatus(
        repositories={
            "camptocamp/test": _TransversalStatusRepo(
                versions={
                    "1.0": _TransversalStatusVersion(
                        support="Best effort",
                        dependencies_by_datasource={
                            "pypi": _TransversalStatusNameInDatasource(
                                versions_by_names={
                                    "other_package": _TransversalStatusVersions(versions=["2.0.1"])
                                },
                            )
                        },
                    )
                },
            ),
            "camptocamp/other": _TransversalStatusRepo(
                versions={
                    "2.0": _TransversalStatusVersion(
                        support="Best effort",
                        names_by_datasource={
                            "pypi": _TransversalStatusNameByDatasource(names=["other_package"])
                        },
                    )
                },
            ),
        }
    )
    context.params = {}
    output = versions.get_transversal_dashboard(context)
    assert output.data == {"repositories": ["camptocamp/test", "camptocamp/other"]}


def test_get_transversal_dashboard_repo() -> None:
    versions = Versions()
    context = Mock()
    context.status = _TransversalStatus(
        repositories={
            "camptocamp/test": _TransversalStatusRepo(
                versions={
                    "1.0": _TransversalStatusVersion(
                        support="Best effort",
                        dependencies_by_datasource={
                            "pypi": _TransversalStatusNameInDatasource(
                                versions_by_names={
                                    "other_package": _TransversalStatusVersions(versions=["2.0.1"])
                                },
                            )
                        },
                    )
                },
            ),
            "camptocamp/other": _TransversalStatusRepo(
                versions={
                    "2.0": _TransversalStatusVersion(
                        support="Best effort",
                        names_by_datasource={
                            "pypi": _TransversalStatusNameByDatasource(names=["other_package"])
                        },
                    )
                },
            ),
        }
    )
    context.params = {"repository": "camptocamp/test"}
    output = versions.get_transversal_dashboard(context)
    assert output.data["dependencies_branches"] == _DependenciesBranches(
        by_branch={
            "1.0": _Dependencies(
                forward=[
                    _Dependency(
                        name="other_package",
                        datasource="pypi",
                        version="2.0.1",
                        support="Best effort",
                        color="--bs-danger",
                        repo="camptocamp/other",
                    )
                ],
                reverse=[
                    _Dependency(
                        name="other_package",
                        datasource="pypi",
                        version="2.0",
                        support="Unsupported",
                        color="--bs-danger",
                        repo="camptocamp/other",
                    )
                ],
            )
        }
    )


def test_docker_datasource() -> None:
    version = "1.2.3"
    result = _canonical_minor_version("docker", version)
    assert result == version


def test_valid_version() -> None:
    version = "1.2.3"
    result = _canonical_minor_version("other", version)
    assert result == "1.2"


def test_invalid_version() -> None:
    version = "invalid"
    result = _canonical_minor_version("other", version)
    assert result == version


@responses.activate
def test__update_upstream_versions() -> None:
    context = Mock()
    context.transversal_status = _TransversalStatus()
    context.module_config = {
        "external-packages": [
            {"package": "package1", "datasource": "datasource1"},
            {"package": "package2", "datasource": "datasource2"},
        ]
    }

    responses.get(
        "https://endoflife.date/api/package1.json",
        json=[
            {
                "eol": "2038-12-31",
                "cycle": "1.0",
            }
        ],
        status=200,
    )
    responses.get(
        "https://endoflife.date/api/package2.json",
        json=[{"eol": "2038-12-31", "cycle": "v1.0"}, {"eol": "2039-12-31", "cycle": "v2.0"}],
        status=200,
    )

    _update_upstream_versions(context)

    assert list(context.transversal_status.updated.keys()) == ["package1", "package2"]
    assert list(context.transversal_status.repositories.keys()) == ["package1", "package2"]
    assert context.transversal_status.repositories["package1"].url == "https://endoflife.date/package1"
    assert context.transversal_status.repositories["package2"].url == "https://endoflife.date/package2"
    assert context.transversal_status.repositories["package1"].versions == {
        "1.0": _TransversalStatusVersion(
            support="2038-12-31",
            names_by_datasource={"datasource1": _TransversalStatusNameByDatasource(names=["package1"])},
        )
    }
    assert context.transversal_status.repositories["package2"].versions == {
        "v1.0": _TransversalStatusVersion(
            support="2038-12-31",
            names_by_datasource={"datasource2": _TransversalStatusNameByDatasource(names=["package2"])},
        ),
        "v2.0": _TransversalStatusVersion(
            support="2039-12-31",
            names_by_datasource={"datasource2": _TransversalStatusNameByDatasource(names=["package2"])},
        ),
    }


def test_read_dependency() -> None:
    json = {
        "packageFiles": {
            "docker-compose": [
                {"deps": [], "packageFile": "docker-compose.override.sample.yaml"},
                {
                    "deps": [
                        {
                            "depName": "camptocamp/postgres",
                            "currentValue": "14-postgis-3",
                            "replaceString": "camptocamp/postgres:14-postgis-3",
                            "autoReplaceStringTemplate": "{{depName}}{{#if newValue}}:{{newValue}}{{/if}}{{#if newDigest}}@{{newDigest}}{{/if}}",
                            "datasource": "docker",
                        }
                    ],
                    "packageFile": "docker-compose.yaml",
                },
            ],
            "dockerfile": [
                {
                    "deps": [
                        {
                            "depName": "ubuntu",
                            "currentValue": "23.10",
                            "replaceString": "ubuntu:23.10",
                            "autoReplaceStringTemplate": "{{depName}}{{#if newValue}}:{{newValue}}{{/if}}{{#if newDigest}}@{{newDigest}}{{/if}}",
                            "datasource": "docker",
                            "versioning": "ubuntu",
                            "depType": "final",
                        }
                    ],
                    "packageFile": "Dockerfile",
                }
            ],
            "github-actions": [
                {
                    "deps": [
                        {
                            "depName": "camptocamp/backport-action",
                            "commitMessageTopic": "{{{depName}}} action",
                            "datasource": "github-tags",
                            "versioning": "docker",
                            "depType": "action",
                            "replaceString": "camptocamp/backport-action@master",
                            "autoReplaceStringTemplate": "{{depName}}@{{#if newDigest}}{{newDigest}}{{#if newValue}} # {{newValue}}{{/if}}{{/if}}{{#unless newDigest}}{{newValue}}{{/unless}}",
                            "currentValue": "master",
                            "skipReason": "github-token-required",
                        },
                        {
                            "depName": "ubuntu",
                            "currentValue": "22.04",
                            "replaceString": "ubuntu-22.04",
                            "depType": "github-runner",
                            "datasource": "github-runners",
                            "autoReplaceStringTemplate": "{{depName}}-{{newValue}}",
                        },
                    ],
                    "packageFile": ".github/workflows/backport.yaml",
                },
            ],
            "html": [
                {
                    "deps": [
                        {
                            "datasource": "cdnjs",
                            "depName": "bootstrap",
                            "packageName": "bootstrap/css/bootstrap.min.css",
                            "currentValue": "5.3.3",
                            "replaceString": '<link\n      rel="stylesheet"\n      href="https://cdnjs.cloudflare.com/ajax/libs/bootstrap/5.3.3/css/bootstrap.min.css"\n      integrity="sha512-jnSuA4Ss2PkkikSOLtYs8BlYIeeIK1h99ty4YfvRPAlzr377vr3CXDb7sb7eEEBYjDtcYj+AjBH3FLv5uSJuXg=="\n      crossorigin="anonymous"\n      referrerpolicy="no-referrer"\n    />',
                            "currentDigest": "sha512-jnSuA4Ss2PkkikSOLtYs8BlYIeeIK1h99ty4YfvRPAlzr377vr3CXDb7sb7eEEBYjDtcYj+AjBH3FLv5uSJuXg==",
                        },
                    ],
                },
            ],
            "npm": [
                {
                    "deps": [
                        {
                            "depType": "dependencies",
                            "depName": "@jamietanna/renovate-graph",
                            "currentValue": "^0.17.0",
                            "datasource": "npm",
                            "prettyDepType": "dependency",
                            "lockedVersion": "0.17.0",
                        },
                        {
                            "depType": "dependencies",
                            "depName": "snyk",
                            "currentValue": "1.1291.0",
                            "datasource": "npm",
                            "prettyDepType": "dependency",
                            "lockedVersion": "1.1291.0",
                        },
                    ],
                    "extractedConstraints": {"npm": ">=7"},
                    "packageFileVersion": "0.0.0",
                    "managerData": {
                        "packageJsonName": "ghci",
                        "hasPackageManager": False,
                        "npmLock": "package-lock.json",
                        "yarnZeroInstall": False,
                    },
                    "skipInstalls": True,
                    "packageFile": "package.json",
                    "lockFiles": ["package-lock.json"],
                }
            ],
            "nvm": [
                {
                    "deps": [{"depName": "node", "currentValue": "20", "datasource": "node-version"}],
                    "packageFile": ".nvmrc",
                }
            ],
            "pep621": [
                {
                    "deps": [
                        {
                            "packageName": "poetry-core",
                            "depName": "poetry-core",
                            "datasource": "pypi",
                            "depType": "build-system.requires",
                            "currentValue": ">=1.0.0",
                        },
                        {
                            "packageName": "poetry-dynamic-versioning",
                            "depName": "poetry-dynamic-versioning",
                            "datasource": "pypi",
                            "depType": "build-system.requires",
                            "skipReason": "unspecified-version",
                        },
                    ],
                    "packageFile": "pyproject.toml",
                }
            ],
            "pip_requirements": [
                {
                    "deps": [
                        {
                            "depName": "c2cciutils",
                            "currentValue": "==1.6.18",
                            "datasource": "pypi",
                            "currentVersion": "1.6.18",
                        },
                        {
                            "depName": "poetry",
                            "currentValue": "==1.8.2",
                            "datasource": "pypi",
                            "currentVersion": "1.8.2",
                        },
                        {"depName": "certifi", "currentValue": ">=2023.7.22", "datasource": "pypi"},
                        {"depName": "setuptools", "currentValue": ">=65.5.1", "datasource": "pypi"},
                        {"depName": "jinja2", "currentValue": ">=3.1.3", "datasource": "pypi"},
                    ],
                    "packageFile": "ci/requirements.txt",
                },
            ],
            "poetry": [
                {
                    "deps": [
                        {
                            "datasource": "github-releases",
                            "currentValue": ">=3.10,<3.12",
                            "managerData": {"nestedVersion": False},
                            "versioning": "pep440",
                            "depName": "python",
                            "depType": "dependencies",
                            "packageName": "containerbase/python-prebuild",
                            "commitMessageTopic": "Python",
                            "registryUrls": None,
                            "skipReason": "github-token-required",
                        },
                        {
                            "datasource": "pypi",
                            "managerData": {"nestedVersion": True},
                            "currentValue": "6.0.8",
                            "versioning": "pep440",
                            "depName": "c2cwsgiutils",
                            "depType": "dependencies",
                            "lockedVersion": "6.0.8",
                        },
                    ],
                    "packageFileVersion": "0.0.0",
                    "extractedConstraints": {"python": ">=3.10,<3.12"},
                    "lockFiles": ["poetry.lock"],
                    "packageFile": "pyproject.toml",
                }
            ],
            "pre-commit": [
                {
                    "deps": [
                        {
                            "datasource": "github-tags",
                            "depName": "pre-commit/mirrors-prettier",
                            "depType": "repository",
                            "packageName": "pre-commit/mirrors-prettier",
                            "currentValue": "v3.1.0",
                            "skipReason": "github-token-required",
                        },
                    ],
                    "packageFile": ".pre-commit-config.yaml",
                }
            ],
            "regex": [
                {
                    "deps": [
                        {
                            "depName": "camptocamp/c2cciutils",
                            "currentValue": "1.6.18",
                            "datasource": "github-tags",
                            "replaceString": "# yaml-language-server: $schema=https://raw.githubusercontent.com/camptocamp/c2cciutils/1.6.18/c2cciutils/schema.json",
                            "skipReason": "github-token-required",
                        }
                    ],
                    "matchStrings": [
                        ".*https://raw\\.githubusercontent\\.com/(?<depName>[^\\s]+)/(?<currentValue>[0-9\\.]+)/.*"
                    ],
                    "datasourceTemplate": "github-tags",
                    "packageFile": "ci/config.yaml",
                },
                {
                    "deps": [
                        {
                            "depName": "prettier",
                            "currentValue": "3.2.5",
                            "datasource": "npm",
                            "replaceString": "          - prettier@3.2.5 # npm",
                        },
                    ],
                    "matchStrings": [
                        " +- '?(?<depName>[^' @=]+)(@|==)(?<currentValue>[^' @=]+)'? # (?<datasource>.+)"
                    ],
                    "packageFile": ".pre-commit-config.yaml",
                },
            ],
        }
    }

    context = Mock()
    context.module_config = {}
    result = {}
    _read_dependencies(context, json, result)
    assert result == {
        "cdnjs": _TransversalStatusNameInDatasource(
            versions_by_names={"bootstrap": _TransversalStatusVersions(versions=["5.3.3"])}
        ),
        "docker": _TransversalStatusNameInDatasource(
            versions_by_names={
                "camptocamp/postgres": _TransversalStatusVersions(versions=["14-postgis-3"]),
                "ubuntu": _TransversalStatusVersions(versions=["23.10"]),
            }
        ),
        "github-releases": _TransversalStatusNameInDatasource(
            versions_by_names={"python": _TransversalStatusVersions(versions=[">=3.10,<3.12"])}
        ),
        "github-runners": _TransversalStatusNameInDatasource(
            versions_by_names={"ubuntu": _TransversalStatusVersions(versions=["22.04"])}
        ),
        "github-tags": _TransversalStatusNameInDatasource(
            versions_by_names={
                "camptocamp/backport-action": _TransversalStatusVersions(versions=["master"]),
                "pre-commit/mirrors-prettier": _TransversalStatusVersions(versions=["v3.1.0"]),
                "camptocamp/c2cciutils": _TransversalStatusVersions(versions=["1.6.18"]),
            }
        ),
        "node-version": _TransversalStatusNameInDatasource(
            versions_by_names={"node": _TransversalStatusVersions(versions=["20"])}
        ),
        "npm": _TransversalStatusNameInDatasource(
            versions_by_names={
                "@jamietanna/renovate-graph": _TransversalStatusVersions(versions=["^0.17.0"]),
                "snyk": _TransversalStatusVersions(versions=["1.1291.0"]),
                "prettier": _TransversalStatusVersions(versions=["3.2.5"]),
            }
        ),
        "pypi": _TransversalStatusNameInDatasource(
            versions_by_names={
                "poetry-core": _TransversalStatusVersions(versions=[">=1.0.0"]),
                "c2cciutils": _TransversalStatusVersions(versions=["==1.6.18"]),
                "poetry": _TransversalStatusVersions(versions=["==1.8.2"]),
                "certifi": _TransversalStatusVersions(versions=[">=2023.7.22"]),
                "setuptools": _TransversalStatusVersions(versions=[">=65.5.1"]),
                "jinja2": _TransversalStatusVersions(versions=[">=3.1.3"]),
                "c2cwsgiutils": _TransversalStatusVersions(versions=["6.0.8"]),
            }
        ),
    }


def test_transversal_status_to_json():
    status = _TransversalStatus(
        updated={}, repositories={"package1": _TransversalStatusRepo(url="url1", versions={})}
    )
    module = Versions()
    assert module.transversal_status_to_json(status) == {
        "repositories": {
            "package1": {
                "url": "url1",
                "versions": {},
            },
        },
        "updated": {},
    }
