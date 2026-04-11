import datetime
import json
import os
from unittest.mock import AsyncMock, MagicMock, Mock

import githubkit.exception
import pytest
from aioresponses import aioresponses

from github_app_geo_project.module.versions import (
    Versions,
    _build_reverse_dependency,
    _canonical_minor_version,
    _Dependencies,
    _DependenciesBranches,
    _Dependency,
    _DependencyReverse,
    _EventData,
    _IntermediateStatus,
    _is_supported,
    _order_versions,
    _parse_support_date,
    _read_dependencies,
    _rebuild_repo_dependencies,
    _support_category,
    _support_cmp,
    _SupportCategory,
    _TransversalStatus,
    _TransversalStatusDependenciesIndex,
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
    context.github_event_data = {"type": "event", "name": "versions-cron"}
    actions = versions.get_actions(context)
    assert len(actions) == 1
    assert actions[0].data == _EventData(step=1)


@pytest.mark.asyncio
async def test_process_step_2() -> None:
    versions = Versions()
    context = Mock()
    context.module_event_data = _EventData(step=2, version="master")

    context.github_project = Mock()
    context.github_project.owner = "camptocamp"
    context.github_project.repository = "test"
    context.module_config = {}

    github = MagicMock()
    context.github_project.aio_github = github
    rest = MagicMock()
    github.rest = rest
    repos = AsyncMock()
    rest.repos = repos
    response = MagicMock()
    response.status_code = 404
    repos.async_get_content.side_effect = githubkit.exception.RequestFailed(response)

    os.environ["TEST"] = "TRUE"
    os.environ["RENOVATE_GRAPH"] = """
DEBUG: Found sourceUrl with multiple branches that should probably be combined into a group
       "sourceUrl": "https://github.com/eslint/eslint",
       "newVersion": "9.26.0",
       "branches": {"renovate/eslint-js-9.x": "@eslint/js", "renovate/eslint-9.x": "eslint"}
DEBUG: packageFiles with updates
       "config": {
         "docker-compose": [
           {
             "deps": [
               {
                 "depName": "actions/checkout",
                 "commitMessageTopic": "{{{depName}}} action",
                 "datasource": "github-tags",
                 "versioning": "docker",
                 "depType": "action",
                 "replaceString": "actions/checkout@v4",
                 "autoReplaceStringTemplate": "{{depName}}@{{#if newDigest}}{{newDigest}}{{#if newValue}} # {{newValue}}{{/if}}{{/if}}{{#unless newDigest}}{{newValue}}{{/unless}}",
                 "currentValue": "v4",
                 "skipReason": "github-token-required"
               }
             ],
             "packageFile": "docker-compose.yaml"
           }
         ]
        }
 WARN: The repository name for {"mode":"full","allowedHeaders":["X-*"],"autodiscoverRepoOrder":null, ...,"regex":{"pinDigests":false},"jsonata":{"pinDigests":false}} was not defined, skipping
DEBUG: defaultWritePackageDataCallback called for local/camptocamp/c2cgeoportal
       "key": {"platform": "local", "organisation": "camptocamp", "repo": "c2cgeoportal"}
DEBUG: writePackageDataToFile called for local/camptocamp/c2cgeoportal
       "key": {"platform": "local", "organisation": "camptocamp", "repo": "c2cgeoportal"},
       "outDir": "out"
 WARN: writePackageDataToFile called for local/camptocamp/c2cgeoportal, but there was no `packageDataDump` provided, likely because the repository failed to scan. Check the logs
       "key": {"platform": "local", "organisation": "camptocamp", "repo": "c2cgeoportal"},
       "outDir": "out"
DEBUG: Determining if we should process repository camptocamp/tilecloud, using GitHub App authentication (repository=camptocamp/tilecloud)
"""
    output = await versions.process(context)
    assert output.updated_transversal_status is True
    assert isinstance(output.intermediate_status, _IntermediateStatus)
    assert json.loads(output.intermediate_status.model_dump_json(indent=2)) == {
        "external_repositories": {},
        "has_security_policy": False,
        "stabilization_versions": [],
        "step": 2,
        "url": None,
        "version": "master",
        "version_dependencies_by_datasource": {
            "github-tags": {
                "versions_by_names": {
                    "actions/checkout": {
                        "versions": [
                            "v4",
                        ],
                    },
                },
            },
        },
        "version_names_by_datasource": {
            "docker": {
                "names": [
                    "ghcr.io/camptocamp/github-app-geo-project:master",
                ],
            },
            "github-release": {
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
        "version_support": {},
    }
    transversal_status = await versions.update_transversal_status(
        context,
        output.intermediate_status,
        _TransversalStatus(
            repositories={
                "camptocamp/test": _TransversalStatusRepo(
                    versions={
                        "master": _TransversalStatusVersion(
                            support={"type": "Best effort"},
                        ),
                    },
                ),
            },
        ),
    )
    assert isinstance(transversal_status, _TransversalStatus)
    transversal_status_json = versions.transversal_status_to_json(transversal_status)
    assert transversal_status_json.get("repositories") == {
        "camptocamp/test": {
            "dependencies_index": {
                "by_datasource": {
                    "github-tags": {
                        "by_dependency": {
                            "actions/checkout": {
                                "branches_by_version": {
                                    "4": {
                                        "branches": [
                                            "master",
                                        ],
                                    },
                                },
                            },
                        },
                    },
                },
            },
            "has_security_policy": False,
            "names_index": {
                "docker": {
                    "ghcr.io/camptocamp/github-app-geo-project:master": {"master": "Best effort"},
                },
                "github-release": {
                    "camptocamp/test": {"master": "Best effort"},
                },
                "npm": {
                    "ghci": {"master": "Best effort"},
                },
                "pypi": {
                    "github-app-geo-project": {"master": "Best effort"},
                },
            },
            "versions": {
                "master": {
                    "dependencies_by_datasource": {
                        "github-tags": {
                            "versions_by_names": {
                                "actions/checkout": {
                                    "versions": [
                                        "v4",
                                    ],
                                },
                            },
                        },
                    },
                    "names_by_datasource": {
                        "docker": {
                            "names": [
                                "ghcr.io/camptocamp/github-app-geo-project:master",
                            ],
                        },
                        "github-release": {
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
                    "support": {"type": "Best effort"},
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
                        support={"type": "Best effort"},
                        dependencies_by_datasource={
                            "pypi": _TransversalStatusNameInDatasource(
                                versions_by_names={
                                    "other_package": _TransversalStatusVersions(versions=["2.0.1"]),
                                },
                            ),
                        },
                    ),
                },
            ),
            "camptocamp/other": _TransversalStatusRepo(
                versions={
                    "2.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        names_by_datasource={
                            "pypi": _TransversalStatusNameByDatasource(names=["other_package"]),
                        },
                    ),
                },
            ),
        },
    )
    context.params = {}
    output = versions.get_transversal_dashboard(context)
    assert output.data == {"repositories": ["camptocamp/test", "camptocamp/other"]}


@pytest.mark.parametrize(
    ("other_support", "expected_color"),
    [
        ("01/01/2044", "--bs-danger"),
        ("01/01/2046", "--bs-body-bg"),
    ],
)
def test_get_transversal_dashboard_repo_forward(other_support: str, expected_color: str) -> None:
    versions = Versions()
    context = Mock()
    context.status = _TransversalStatus(
        repositories={
            "camptocamp/test": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "01/01/2045", "until": datetime.date(2045, 1, 1)},
                        dependencies_by_datasource={
                            "pypi": _TransversalStatusNameInDatasource(
                                versions_by_names={
                                    "other_package": _TransversalStatusVersions(versions=["2.0.1"]),
                                },
                            ),
                        },
                    ),
                },
            ),
            "camptocamp/other": _TransversalStatusRepo(
                has_security_policy=True,
                names_index={
                    "pypi": {
                        "other_package": {
                            "2.0": other_support,
                        },
                    },
                },
                versions={
                    "2.0": _TransversalStatusVersion(
                        support=other_support,
                        names_by_datasource={
                            "pypi": _TransversalStatusNameByDatasource(names=["other_package"]),
                        },
                    ),
                },
            ),
        },
    )
    context.params = {"repository": "camptocamp/test"}
    output = versions.get_transversal_dashboard(context)
    assert output.data["dependencies_branches"] == _DependenciesBranches(
        by_branch={
            "1.0": _Dependencies(
                support={"type": "01/01/2045", "until": datetime.date(2045, 1, 1)},
                color="--bs-body-bg",
                forward=[
                    _Dependency(
                        name="other_package",
                        datasource="pypi",
                        version="2.0 (2.0.1)",
                        support=other_support,
                        color=expected_color,
                        repo="camptocamp/other",
                    ),
                ],
                reverse=[],
            ),
        },
    )


def test_get_transversal_dashboard_repo_forward_docker() -> None:
    versions = Versions()
    context = Mock()
    context.status = _TransversalStatus(
        repositories={
            "camptocamp/test": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        dependencies_by_datasource={
                            "docker": _TransversalStatusNameInDatasource(
                                versions_by_names={
                                    "camptocamp/other": _TransversalStatusVersions(versions=["2.0"]),
                                },
                            ),
                        },
                    ),
                },
            ),
            "camptocamp/other": _TransversalStatusRepo(
                has_security_policy=True,
                names_index={
                    "docker": {
                        "camptocamp/other:2.0": {
                            "2.0": "Best effort",
                        },
                    },
                },
                versions={
                    "2.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        names_by_datasource={
                            "docker": _TransversalStatusNameByDatasource(names=["camptocamp/other:2.0"]),
                        },
                    ),
                },
            ),
        },
    )
    context.params = {"repository": "camptocamp/test"}
    output = versions.get_transversal_dashboard(context)
    assert output.data["dependencies_branches"] == _DependenciesBranches(
        by_branch={
            "1.0": _Dependencies(
                support={"type": "Best effort"},
                color="--bs-body-bg",
                forward=[
                    _Dependency(
                        name="camptocamp/other",
                        datasource="docker",
                        version="2.0",
                        support={"type": "Best effort"},
                        color="--bs-body-bg",
                        repo="camptocamp/other",
                    ),
                ],
                reverse=[],
            ),
        },
    )


def test_get_transversal_dashboard_repo_forward_docker_2() -> None:
    versions = Versions()
    context = Mock()
    context.status = _TransversalStatus(
        repositories={
            "camptocamp/test": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "27/06/2027", "until": datetime.date(2027, 6, 27)},
                        dependencies_by_datasource={
                            "docker": _TransversalStatusNameInDatasource(
                                versions_by_names={
                                    "ghcr.io/osgeo/gdal": _TransversalStatusVersions(
                                        versions=["ubuntu-small-3.8.5"],
                                    ),
                                },
                            ),
                        },
                    ),
                },
            ),
            "camptocamp/other": _TransversalStatusRepo(
                has_security_policy=True,
                names_index={
                    "docker": {
                        "osgeo/gdal:ubuntu-small-3.8.5": {
                            "2.0": "Best effort",
                        },
                        "ghcr.io/osgeo/gdal:ubuntu-small-3.8.5": {
                            "2.0": "Best effort",
                        },
                    },
                },
                versions={
                    "2.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        names_by_datasource={
                            "docker": _TransversalStatusNameByDatasource(
                                names=[
                                    "osgeo/gdal:ubuntu-small-3.8.5",
                                    "ghcr.io/osgeo/gdal:ubuntu-small-3.8.5",
                                ],
                            ),
                        },
                    ),
                },
            ),
        },
    )
    context.params = {"repository": "camptocamp/test"}
    output = versions.get_transversal_dashboard(context)
    assert output.data["dependencies_branches"] == _DependenciesBranches(
        by_branch={
            "1.0": _Dependencies(
                support={"type": "27/06/2027", "until": datetime.date(2027, 6, 27)},
                color="--bs-body-bg",
                forward=[
                    _Dependency(
                        name="ghcr.io/osgeo/gdal",
                        datasource="docker",
                        version="ubuntu-small-3.8.5",
                        support={"type": "Best effort"},
                        color="--bs-danger",
                        repo="camptocamp/other",
                    ),
                ],
                reverse=[],
            ),
        },
    )


def test_get_transversal_dashboard_repo_forward_docker_double() -> None:
    versions = Versions()
    context = Mock()
    context.status = _TransversalStatus(
        repositories={
            "camptocamp/test": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        dependencies_by_datasource={
                            "docker": _TransversalStatusNameInDatasource(
                                versions_by_names={
                                    "camptocamp/other": _TransversalStatusVersions(versions=["1.0", "2.0"]),
                                },
                            ),
                        },
                    ),
                },
            ),
            "camptocamp/other": _TransversalStatusRepo(
                has_security_policy=True,
                names_index={
                    "docker": {
                        "camptocamp/other:1.0": {
                            "1.0": "Best effort",
                        },
                        "camptocamp/other:2.0": {
                            "2.0": "Best effort",
                        },
                    },
                },
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        names_by_datasource={
                            "docker": _TransversalStatusNameByDatasource(names=["camptocamp/other:1.0"]),
                        },
                    ),
                    "2.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        names_by_datasource={
                            "docker": _TransversalStatusNameByDatasource(names=["camptocamp/other:2.0"]),
                        },
                    ),
                },
            ),
        },
    )
    context.params = {"repository": "camptocamp/test"}
    output = versions.get_transversal_dashboard(context)
    assert output.data["dependencies_branches"] == _DependenciesBranches(
        by_branch={
            "1.0": _Dependencies(
                support={"type": "Best effort"},
                color="--bs-body-bg",
                forward=[
                    _Dependency(
                        name="camptocamp/other",
                        datasource="docker",
                        version="1.0",
                        support={"type": "Best effort"},
                        color="--bs-body-bg",
                        repo="camptocamp/other",
                    ),
                    _Dependency(
                        name="camptocamp/other",
                        datasource="docker",
                        version="2.0",
                        support={"type": "Best effort"},
                        color="--bs-body-bg",
                        repo="camptocamp/other",
                    ),
                ],
                reverse=[],
            ),
        },
    )


def test_get_transversal_dashboard_repo_forward_docker_multiple_statuses() -> None:
    """A single docker image tag published by two branches with different support levels.

    The most restrictive status should be selected.
    """
    versions = Versions()
    context = Mock()
    context.status = _TransversalStatus(
        repositories={
            "camptocamp/test": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "27/06/2027", "until": datetime.date(2027, 6, 27)},
                        dependencies_by_datasource={
                            "docker": _TransversalStatusNameInDatasource(
                                versions_by_names={
                                    "camptocamp/other": _TransversalStatusVersions(versions=["latest"]),
                                },
                            ),
                        },
                    ),
                },
            ),
            "camptocamp/other": _TransversalStatusRepo(
                has_security_policy=True,
                names_index={
                    "docker": {
                        "camptocamp/other:latest": {
                            "1.0": "Best effort",
                            "2.0": "Unsupported",
                        },
                    },
                },
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        names_by_datasource={
                            "docker": _TransversalStatusNameByDatasource(
                                names=["camptocamp/other:latest"],
                            ),
                        },
                    ),
                    "2.0": _TransversalStatusVersion(
                        support={"type": "Unsupported"},
                        names_by_datasource={
                            "docker": _TransversalStatusNameByDatasource(
                                names=["camptocamp/other:latest"],
                            ),
                        },
                    ),
                },
            ),
        },
    )
    context.params = {"repository": "camptocamp/test"}
    output = versions.get_transversal_dashboard(context)
    assert output.data["dependencies_branches"] == _DependenciesBranches(
        by_branch={
            "1.0": _Dependencies(
                support={"type": "27/06/2027", "until": datetime.date(2027, 6, 27)},
                color="--bs-body-bg",
                forward=[
                    _Dependency(
                        name="camptocamp/other",
                        datasource="docker",
                        version="latest",
                        support={"type": "Unsupported"},
                        color="--bs-danger",
                        repo="camptocamp/other",
                    ),
                ],
                reverse=[],
            ),
        },
    )


def test_get_transversal_dashboard_repo_forward_docker_empty_status() -> None:
    """A docker image tag present in the names index but with no status_by_version entries.

    The fallback "No support defined" status should be used.
    """
    versions = Versions()
    context = Mock()
    context.status = _TransversalStatus(
        repositories={
            "camptocamp/test": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "27/06/2027", "until": datetime.date(2027, 6, 27)},
                        dependencies_by_datasource={
                            "docker": _TransversalStatusNameInDatasource(
                                versions_by_names={
                                    "camptocamp/other": _TransversalStatusVersions(versions=["latest"]),
                                },
                            ),
                        },
                    ),
                },
            ),
            "camptocamp/other": _TransversalStatusRepo(
                has_security_policy=True,
                names_index={
                    "docker": {
                        "camptocamp/other:latest": {},
                    },
                },
            ),
        },
    )
    context.params = {"repository": "camptocamp/test"}
    output = versions.get_transversal_dashboard(context)
    assert output.data["dependencies_branches"] == _DependenciesBranches(
        by_branch={
            "1.0": _Dependencies(
                support={"type": "27/06/2027", "until": datetime.date(2027, 6, 27)},
                color="--bs-body-bg",
                forward=[
                    _Dependency(
                        name="camptocamp/other",
                        datasource="docker",
                        version="latest",
                        support={"type": "No support defined"},
                        color="--bs-body-bg",
                        repo="camptocamp/other",
                    ),
                ],
                reverse=[],
            ),
        },
    )


def test_get_transversal_dashboard_repo_forward_inexisting() -> None:
    versions = Versions()
    context = Mock()
    context.status = _TransversalStatus(
        repositories={
            "camptocamp/test": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        dependencies_by_datasource={
                            "pypi": _TransversalStatusNameInDatasource(
                                versions_by_names={
                                    "other_package": _TransversalStatusVersions(versions=["2.0.1"]),
                                },
                            ),
                        },
                    ),
                },
            ),
            "camptocamp/other": _TransversalStatusRepo(
                has_security_policy=True,
                names_index={
                    "pypi": {
                        "other_package": {
                            "3.0": "Best effort",
                        },
                    },
                },
                versions={
                    "3.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        names_by_datasource={
                            "pypi": _TransversalStatusNameByDatasource(names=["other_package"]),
                        },
                    ),
                },
            ),
        },
    )
    context.params = {"repository": "camptocamp/test"}
    output = versions.get_transversal_dashboard(context)
    assert output.data["dependencies_branches"] == _DependenciesBranches(
        by_branch={
            "1.0": _Dependencies(
                support={"type": "Best effort"},
                color="--bs-body-bg",
                forward=[
                    _Dependency(
                        name="other_package",
                        datasource="pypi",
                        version="2.0 (2.0.1)",
                        support={"type": "Unsupported"},
                        color="--bs-danger",
                        repo="camptocamp/other",
                    ),
                ],
                reverse=[],
            ),
        },
    )


def test_get_transversal_dashboard_repo_forward_no_support() -> None:
    versions = Versions()
    context = Mock()
    context.status = _TransversalStatus(
        repositories={
            "camptocamp/test": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "01/01/2045", "until": datetime.date(2045, 1, 1)},
                        dependencies_by_datasource={
                            "pypi": _TransversalStatusNameInDatasource(
                                versions_by_names={
                                    "other_package": _TransversalStatusVersions(versions=["2.0.1"]),
                                },
                            ),
                        },
                    ),
                },
            ),
            "camptocamp/other": _TransversalStatusRepo(
                has_security_policy=False,
                names_index={
                    "pypi": {
                        "other_package": {
                            "master": "Best effort",
                        },
                    },
                },
                versions={
                    "master": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        names_by_datasource={
                            "pypi": _TransversalStatusNameByDatasource(names=["other_package"]),
                        },
                    ),
                },
            ),
        },
    )
    context.params = {"repository": "camptocamp/test"}
    output = versions.get_transversal_dashboard(context)
    assert output.data["dependencies_branches"] == _DependenciesBranches(
        by_branch={
            "1.0": _Dependencies(
                support={"type": "01/01/2045", "until": datetime.date(2045, 1, 1)},
                color="--bs-body-bg",
                forward=[
                    _Dependency(
                        name="other_package",
                        datasource="pypi",
                        version="2.0 (2.0.1)",
                        support={"type": "No support defined"},
                        color="--bs-body-bg",
                        repo="camptocamp/other",
                    ),
                ],
                reverse=[],
            ),
        },
    )


def test_get_transversal_dashboard_repo_forward_no_support_version() -> None:
    versions = Versions()
    context = Mock()
    context.status = _TransversalStatus(
        repositories={
            "camptocamp/test": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "01/01/2045", "until": datetime.date(2045, 1, 1)},
                        dependencies_by_datasource={
                            "pypi": _TransversalStatusNameInDatasource(
                                versions_by_names={
                                    "other_package": _TransversalStatusVersions(versions=["2.0.1"]),
                                },
                            ),
                        },
                    ),
                },
            ),
            "camptocamp/other": _TransversalStatusRepo(
                has_security_policy=True,
                names_index={
                    "pypi": {
                        "other_package": {
                            "1.0": "01/01/2045",
                        },
                    },
                },
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "01/01/2045", "until": datetime.date(2045, 1, 1)},
                        names_by_datasource={
                            "pypi": _TransversalStatusNameByDatasource(names=["other_package"]),
                        },
                    ),
                },
            ),
        },
    )
    context.params = {"repository": "camptocamp/test"}
    output = versions.get_transversal_dashboard(context)
    assert output.data["dependencies_branches"] == _DependenciesBranches(
        by_branch={
            "1.0": _Dependencies(
                support={"type": "01/01/2045", "until": datetime.date(2045, 1, 1)},
                color="--bs-body-bg",
                forward=[
                    _Dependency(
                        name="other_package",
                        datasource="pypi",
                        version="2.0 (2.0.1)",
                        support={"type": "Unsupported"},
                        color="--bs-danger",
                        repo="camptocamp/other",
                    ),
                ],
                reverse=[],
            ),
        },
    )


def test_get_transversal_dashboard_repo_forward_no_package() -> None:
    versions = Versions()
    context = Mock()
    context.status = _TransversalStatus(
        repositories={
            "camptocamp/test": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "01/01/2045", "until": datetime.date(2045, 1, 1)},
                        dependencies_by_datasource={
                            "pypi": _TransversalStatusNameInDatasource(
                                versions_by_names={
                                    "other_package": _TransversalStatusVersions(versions=["2.0.1"]),
                                },
                            ),
                        },
                    ),
                },
            ),
        },
    )
    context.params = {"repository": "camptocamp/test"}
    output = versions.get_transversal_dashboard(context)
    assert output.data["dependencies_branches"] == _DependenciesBranches(
        by_branch={
            "1.0": _Dependencies(
                support={"type": "01/01/2045", "until": datetime.date(2045, 1, 1)},
                color="--bs-body-bg",
                forward=[],
                reverse=[],
            ),
        },
    )


@pytest.mark.parametrize(
    ("other_support", "expected_color"),
    [("01/01/2044", "--bs-body-bg"), ("01/01/2046", "--bs-danger")],
)
def test_get_transversal_dashboard_repo_reverse(other_support: str, expected_color: str) -> None:
    versions = Versions()
    context = Mock()
    context.status = _TransversalStatus(
        repositories={
            "camptocamp/test": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "01/01/2045", "until": datetime.date(2045, 1, 1)},
                        names_by_datasource={"pypi": _TransversalStatusNameByDatasource(names=["test"])},
                    ),
                },
            ),
            "camptocamp/other": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "2.0": _TransversalStatusVersion(
                        support=other_support,
                        dependencies_by_datasource={
                            "pypi": _TransversalStatusNameInDatasource(
                                versions_by_names={"test": _TransversalStatusVersions(versions=["1.0.1"])},
                            ),
                        },
                    ),
                },
            ),
        },
    )
    _rebuild_repo_dependencies(context.status.repositories["camptocamp/other"])
    context.params = {"repository": "camptocamp/test"}
    output = versions.get_transversal_dashboard(context)
    assert output.data["dependencies_branches"] == _DependenciesBranches(
        by_branch={
            "1.0": _Dependencies(
                support={"type": "01/01/2045", "until": datetime.date(2045, 1, 1)},
                color="--bs-body-bg",
                forward=[],
                reverse=[
                    _DependencyReverse(
                        name="camptocamp/other",
                        version="2.0",
                        support=other_support,
                        color=expected_color,
                        repo="camptocamp/other",
                    ),
                ],
            ),
        },
    )


def test_get_transversal_dashboard_repo_reverse_docker() -> None:
    versions = Versions()
    context = Mock()
    context.status = _TransversalStatus(
        repositories={
            "camptocamp/test": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        names_by_datasource={
                            "docker": _TransversalStatusNameByDatasource(names=["camptocamp/test:1.0"]),
                        },
                    ),
                },
            ),
            "camptocamp/other": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "2.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        dependencies_by_datasource={
                            "docker": _TransversalStatusNameInDatasource(
                                versions_by_names={
                                    "camptocamp/test": _TransversalStatusVersions(versions=["1.0"]),
                                },
                            ),
                        },
                    ),
                },
            ),
        },
    )
    _rebuild_repo_dependencies(context.status.repositories["camptocamp/other"])
    context.params = {"repository": "camptocamp/test"}
    output = versions.get_transversal_dashboard(context)
    assert output.data["dependencies_branches"] == _DependenciesBranches(
        by_branch={
            "1.0": _Dependencies(
                support={"type": "Best effort"},
                color="--bs-body-bg",
                forward=[],
                reverse=[
                    _DependencyReverse(
                        name="camptocamp/other",
                        version="2.0",
                        support={"type": "Best effort"},
                        color="--bs-body-bg",
                        repo="camptocamp/other",
                    ),
                ],
            ),
        },
    )


def test_get_transversal_dashboard_repo_reverse_docker_different() -> None:
    versions = Versions()
    context = Mock()
    context.status = _TransversalStatus(
        repositories={
            "camptocamp/test": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        names_by_datasource={
                            "docker": _TransversalStatusNameByDatasource(
                                names=["camptocamp/test:prefix-1.0"],
                            ),
                        },
                    ),
                },
            ),
            "camptocamp/other": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "2.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        dependencies_by_datasource={
                            "docker": _TransversalStatusNameInDatasource(
                                versions_by_names={
                                    "camptocamp/test": _TransversalStatusVersions(versions=["prefix-1.0"]),
                                },
                            ),
                        },
                    ),
                },
            ),
        },
    )
    _rebuild_repo_dependencies(context.status.repositories["camptocamp/other"])
    context.params = {"repository": "camptocamp/test"}
    output = versions.get_transversal_dashboard(context)
    assert output.data["dependencies_branches"] == _DependenciesBranches(
        by_branch={
            "1.0": _Dependencies(
                support={"type": "Best effort"},
                color="--bs-body-bg",
                forward=[],
                reverse=[
                    _DependencyReverse(
                        name="camptocamp/other",
                        version="2.0",
                        support={"type": "Best effort"},
                        color="--bs-body-bg",
                        repo="camptocamp/other",
                    ),
                ],
            ),
        },
    )


def test_get_transversal_dashboard_repo_reverse_docker_alternate_tag() -> None:
    versions = Versions()
    context = Mock()
    context.status = _TransversalStatus(
        repositories={
            "camptocamp/test": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        names_by_datasource={
                            "docker": _TransversalStatusNameByDatasource(
                                names=["camptocamp/test:1.0", "camptocamp/test:latest"],
                            ),
                        },
                    ),
                },
            ),
            "camptocamp/other": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "2.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        dependencies_by_datasource={},
                    ),
                },
                dependencies_index=_TransversalStatusDependenciesIndex(
                    by_datasource={
                        "docker": {
                            "by_dependency": {
                                "camptocamp/test:latest": {
                                    "branches_by_version": {
                                        "latest": {
                                            "branches": ["2.0"],
                                        },
                                    },
                                },
                            },
                        },
                    },
                ),
            ),
        },
    )
    context.params = {"repository": "camptocamp/test"}

    output = versions.get_transversal_dashboard(context)

    assert output.data["dependencies_branches"] == _DependenciesBranches(
        by_branch={
            "1.0": _Dependencies(
                support={"type": "Best effort"},
                color="--bs-body-bg",
                forward=[],
                reverse=[
                    _DependencyReverse(
                        name="camptocamp/other",
                        version="2.0",
                        support={"type": "Best effort"},
                        color="--bs-body-bg",
                        repo="camptocamp/other",
                    ),
                ],
            ),
        },
    )


def test_get_transversal_dashboard_repo_reverse_unexisting() -> None:
    versions = Versions()
    context = Mock()
    context.status = _TransversalStatus(
        repositories={
            "camptocamp/test": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        names_by_datasource={"pypi": _TransversalStatusNameByDatasource(names=["test"])},
                    ),
                },
            ),
            "camptocamp/other": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "2.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        dependencies_by_datasource={
                            "pypi": _TransversalStatusNameInDatasource(
                                versions_by_names={"test": _TransversalStatusVersions(versions=["2.0.1"])},
                            ),
                        },
                    ),
                },
            ),
        },
    )
    _rebuild_repo_dependencies(context.status.repositories["camptocamp/other"])
    context.params = {"repository": "camptocamp/test"}
    output = versions.get_transversal_dashboard(context)
    assert output.data["dependencies_branches"] == _DependenciesBranches(
        by_branch={
            "1.0": _Dependencies(
                support={"type": "Best effort"},
                color="--bs-body-bg",
            ),
            "2.0": _Dependencies(
                support={"type": "Unsupported"},
                color="--bs-danger",
                forward=[],
                reverse=[
                    _DependencyReverse(
                        name="camptocamp/other",
                        version="2.0",
                        support={"type": "Best effort"},
                        color="--bs-danger",
                        repo="camptocamp/other",
                    ),
                ],
            ),
        },
    )


def test_get_transversal_dashboard_repo_external() -> None:
    versions = Versions()
    context = Mock()
    context.status = _TransversalStatus(
        repositories={
            "camptocamp/test": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        dependencies_by_datasource={
                            "pypi": _TransversalStatusNameInDatasource(
                                versions_by_names={"other": _TransversalStatusVersions(versions=["2.0.1"])},
                            ),
                        },
                    ),
                },
            ),
        },
    )
    context.params = {"repository": "camptocamp/test"}
    output = versions.get_transversal_dashboard(context)
    assert output.data["dependencies_branches"] == _DependenciesBranches(
        by_branch={
            "1.0": _Dependencies(
                support={"type": "Best effort"},
                color="--bs-body-bg",
                forward=[],
                reverse=[],
            ),
        },
    )


# parametrize
@pytest.mark.parametrize(
    ("datasource", "package"),
    [
        ("pypi", "wring"),
        ("wrong", "test"),
    ],
)
def test_get_transversal_dashboard_repo_reverse_other(datasource: str, package: str) -> None:
    versions = Versions()
    context = Mock()
    context.status = _TransversalStatus(
        repositories={
            "camptocamp/test": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        names_by_datasource={"pypi": _TransversalStatusNameByDatasource(names=["test"])},
                    ),
                },
            ),
            "camptocamp/other": _TransversalStatusRepo(
                has_security_policy=True,
                versions={
                    "2.0": _TransversalStatusVersion(
                        support={"type": "Best effort"},
                        dependencies_by_datasource={
                            datasource: _TransversalStatusNameInDatasource(
                                versions_by_names={package: _TransversalStatusVersions(versions=["1.0.1"])},
                            ),
                        },
                    ),
                },
            ),
        },
    )
    context.params = {"repository": "camptocamp/test"}
    output = versions.get_transversal_dashboard(context)
    assert output.data["dependencies_branches"] == _DependenciesBranches(
        by_branch={
            "1.0": _Dependencies(
                support={"type": "Best effort"},
                color="--bs-body-bg",
                forward=[],
                reverse=[],
            ),
        },
    )


@pytest.mark.parametrize(
    ("datasource", "version", "expected"),
    [
        ("docker", "1.2.3", "1.2.3"),
        ("other", "1.2.3", "1.2"),
        ("other", "invalid", "invalid"),
        ("other", "^3.7.2", "3.7"),
        ("other", ">=3.9,<4.0", "3.9"),
        ("other", "==1.5.2", "1.5"),
        ("other", ">=3.9.1", "3.9"),
    ],
)
def test_canonical_minor_version(datasource, version, expected) -> None:
    result = _canonical_minor_version(datasource, version)
    assert result == expected


@pytest.mark.asyncio
async def test_update_upstream_versions() -> None:
    with aioresponses() as responses:
        context = Mock()
        context.transversal_status = _TransversalStatus()
        context.module_config = {
            "external-packages": [
                {"package": "package1", "datasource": "datasource1"},
                {"package": "package2", "datasource": "datasource2"},
            ],
        }

        responses.get(
            "https://endoflife.date/api/package1.json",
            payload=[
                {
                    "eol": "2038-12-31",
                    "cycle": "1.0",
                },
            ],
            status=200,
        )
        responses.get(
            "https://endoflife.date/api/package2.json",
            payload=[{"eol": "2038-12-31", "cycle": "v1.0"}, {"eol": "2039-12-31", "cycle": "v2.0"}],
            status=200,
        )

        module = Versions()
        intermediate_status = _IntermediateStatus(step=1)
        await _update_upstream_versions(context, intermediate_status)
        transversal_status = _TransversalStatus()
        await module.update_transversal_status(context, intermediate_status, transversal_status)

        for package in (
            "endoflife.date/package1",
            "endoflife.date/package2",
        ):
            assert package in transversal_status.updated
            assert package in transversal_status.repositories
        assert (
            transversal_status.repositories["endoflife.date/package1"].url
            == "https://endoflife.date/package1"
        )
        assert (
            transversal_status.repositories["endoflife.date/package2"].url
            == "https://endoflife.date/package2"
        )
        assert transversal_status.repositories["endoflife.date/package1"].versions == {
            "1.0": _TransversalStatusVersion(
                support={
                    "type": "31/12/2038",
                    "until": datetime.date(2038, 12, 31),
                },
                names_by_datasource={"datasource1": _TransversalStatusNameByDatasource(names=["package1"])},
            ),
        }
        assert transversal_status.repositories["endoflife.date/package2"].versions == {
            "v1.0": _TransversalStatusVersion(
                support={
                    "type": "31/12/2038",
                    "until": datetime.date(2038, 12, 31),
                },
                names_by_datasource={"datasource2": _TransversalStatusNameByDatasource(names=["package2"])},
            ),
            "v2.0": _TransversalStatusVersion(
                support={
                    "type": "31/12/2039",
                    "until": datetime.date(2039, 12, 31),
                },
                names_by_datasource={"datasource2": _TransversalStatusNameByDatasource(names=["package2"])},
            ),
        }


def test_read_dependency() -> None:
    json = {
        "config": {
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
                        },
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
                        },
                    ],
                    "packageFile": "Dockerfile",
                },
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
                },
            ],
            "nvm": [
                {
                    "deps": [{"depName": "node", "currentValue": "20", "datasource": "node-version"}],
                    "packageFile": ".nvmrc",
                },
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
                },
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
                },
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
                },
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
                        },
                    ],
                    "matchStrings": [
                        ".*https://raw\\.githubusercontent\\.com/(?<depName>[^\\s]+)/(?<currentValue>[0-9\\.]+)/.*",
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
                        " +- '?(?<depName>[^' @=]+)(@|==)(?<currentValue>[^' @=]+)'? # (?<datasource>.+)",
                    ],
                    "packageFile": ".pre-commit-config.yaml",
                },
            ],
        },
    }

    context = Mock()
    context.module_config = {}
    result: dict[str, _TransversalStatusNameInDatasource] = {}
    _read_dependencies(context, json, result)
    assert result == {
        "cdnjs": _TransversalStatusNameInDatasource(
            versions_by_names={"bootstrap": _TransversalStatusVersions(versions=["5.3.3"])},
        ),
        "docker": _TransversalStatusNameInDatasource(
            versions_by_names={
                "camptocamp/postgres": _TransversalStatusVersions(versions=["14-postgis-3"]),
                "ubuntu": _TransversalStatusVersions(versions=["23.10"]),
            },
        ),
        "github-releases": _TransversalStatusNameInDatasource(
            versions_by_names={"python": _TransversalStatusVersions(versions=[">=3.10,<3.12"])},
        ),
        "github-runners": _TransversalStatusNameInDatasource(
            versions_by_names={"ubuntu": _TransversalStatusVersions(versions=["22.04"])},
        ),
        "github-tags": _TransversalStatusNameInDatasource(
            versions_by_names={
                "camptocamp/backport-action": _TransversalStatusVersions(versions=["master"]),
                "pre-commit/mirrors-prettier": _TransversalStatusVersions(versions=["v3.1.0"]),
                "camptocamp/c2cciutils": _TransversalStatusVersions(versions=["1.6.18"]),
            },
        ),
        "node-version": _TransversalStatusNameInDatasource(
            versions_by_names={"node": _TransversalStatusVersions(versions=["20"])},
        ),
        "npm": _TransversalStatusNameInDatasource(
            versions_by_names={
                "@jamietanna/renovate-graph": _TransversalStatusVersions(versions=["^0.17.0"]),
                "snyk": _TransversalStatusVersions(versions=["1.1291.0"]),
                "prettier": _TransversalStatusVersions(versions=["3.2.5"]),
            },
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
            },
        ),
    }


def test_transversal_status_to_json():
    status = _TransversalStatus(
        updated={},
        repositories={"package1": _TransversalStatusRepo(url="url1", versions={})},
    )
    module = Versions()
    assert module.transversal_status_to_json(status) == {
        "repositories": {
            "package1": {
                "dependencies_index": {"by_datasource": {}},
                "has_security_policy": False,
                "names_index": {},
                "url": "url1",
                "versions": {},
            },
        },
        "updated": {},
    }


def test_order_versions():
    versions = ["1.0", "2.0", "1.5", "master", "3.0", "1.2", "1.15"]
    ordered_versions = _order_versions(versions)
    assert ordered_versions == ["master", "3.0", "2.0", "1.15", "1.5", "1.2", "1.0"]


def test_order_versions_single_integer():
    # Single-integer versions like "20" (from node-version datasource) must sort
    # among numeric versions, not before them like branch names.
    versions = ["20", "18", "3.11", "master", "21"]
    ordered_versions = _order_versions(versions)
    assert ordered_versions == ["master", "21", "20", "18", "3.11"]


def test_order_versions_three_part():
    # X.Y.Z versions are treated as numeric (compared by major.minor.patch).
    versions = ["1.2.3", "1.2.10", "1.10.0", "master", "2.0.0"]
    ordered_versions = _order_versions(versions)
    assert ordered_versions == ["master", "2.0.0", "1.10.0", "1.2.10", "1.2.3"]


def test_order_versions_leading_zeros():
    # "1.05" and "1.5" have the same numeric value; comparison must be consistent
    versions = ["1.05", "1.5"]
    ordered_versions = _order_versions(versions)
    # Both represent the same numeric version; order must be stable and not raise
    assert set(ordered_versions) == {"1.05", "1.5"}

    versions = ["1.1.05", "1.1.5"]
    ordered_versions = _order_versions(versions)
    # Both represent the same numeric version; order must be stable and not raise
    assert set(ordered_versions) == {"1.1.05", "1.1.5"}


def test_rebuild_repo_dependencies_index() -> None:
    repo = _TransversalStatusRepo(
        versions={
            "1.2": _TransversalStatusVersion(
                dependencies_by_datasource={
                    "pypi": _TransversalStatusNameInDatasource(
                        versions_by_names={
                            "pkg": _TransversalStatusVersions(versions=["1.2.3"]),
                        },
                    ),
                },
            ),
        },
    )

    _rebuild_repo_dependencies(repo)

    assert repo.dependencies_index.model_dump() == {
        "by_datasource": {
            "pypi": {
                "by_dependency": {
                    "pkg": {
                        "branches_by_version": {
                            "1.2": {
                                "branches": ["1.2"],
                            },
                        },
                    },
                },
            },
        },
    }


def test_build_reverse_dependency_uses_dependencies_index() -> None:
    target_repo = _TransversalStatusRepo(
        versions={
            "1.2": _TransversalStatusVersion(
                support={"type": "Best effort"},
                names_by_datasource={
                    "pypi": _TransversalStatusNameByDatasource(names=["pkg"]),
                },
            ),
        },
        has_security_policy=True,
    )
    dependent_repo = _TransversalStatusRepo(
        versions={
            "main": _TransversalStatusVersion(
                support={"type": "Best effort"},
                dependencies_by_datasource={},
            ),
        },
    )
    dependent_repo.dependencies_index = _TransversalStatusDependenciesIndex(
        by_datasource={
            "pypi": {
                "by_dependency": {
                    "pkg": {
                        "branches_by_version": {
                            "1.2": {
                                "branches": ["main"],
                            },
                        },
                    },
                },
            },
        },
    )

    transversal_status = _TransversalStatus(
        repositories={
            "org/target": target_repo,
            "org/dependent": dependent_repo,
        },
    )

    dependencies_branches = _DependenciesBranches()
    _build_reverse_dependency(
        "org/target",
        target_repo,
        transversal_status,
        dependencies_branches,
    )

    assert dependencies_branches.by_branch["1.2"].reverse[0] == _DependencyReverse(
        name="org/dependent",
        version="main",
        support={"type": "Best effort"},
        color="--bs-body-bg",
        repo="org/dependent",
    )


def test_build_reverse_dependency_uses_dependencies_index_docker() -> None:
    target_repo = _TransversalStatusRepo(
        versions={
            "1.2": _TransversalStatusVersion(
                support={"type": "Best effort"},
                names_by_datasource={
                    "docker": _TransversalStatusNameByDatasource(names=["image:latest"]),
                },
            ),
        },
        has_security_policy=True,
    )
    dependent_repo = _TransversalStatusRepo(
        versions={
            "main": _TransversalStatusVersion(
                support={"type": "Best effort"},
                dependencies_by_datasource={},
            ),
        },
    )
    dependent_repo.dependencies_index = _TransversalStatusDependenciesIndex(
        by_datasource={
            "docker": {
                "by_dependency": {
                    "image:latest": {
                        "branches_by_version": {
                            "1.2": {
                                "branches": ["main"],
                            },
                        },
                    },
                },
            },
        },
    )

    transversal_status = _TransversalStatus(
        repositories={
            "org/target": target_repo,
            "org/dependent": dependent_repo,
        },
    )

    dependencies_branches = _DependenciesBranches()
    _build_reverse_dependency(
        "org/target",
        target_repo,
        transversal_status,
        dependencies_branches,
    )

    assert dependencies_branches.by_branch["1.2"].reverse[0] == _DependencyReverse(
        name="org/dependent",
        version="main",
        support={"type": "Best effort"},
        color="--bs-body-bg",
        repo="org/dependent",
    )


@pytest.mark.parametrize(
    ("support", "dependency_support", "expected_result"),
    [
        ("test", "test", True),
        ("other", "other", True),
        ("other", "test", True),
        ("test", "other", True),
        ("Best effort", "To be defined", True),
        ("To be defined", "Best effort", False),
        ("Unsupported", "Best effort", True),
        ("Best effort", "Unsupported", False),
        ("To be defined", "2040-01-01", True),
        ("Unsupported", "2040-01-01", True),
        ("Best effort", "2040-01-01", True),
        ("2040-01-01", "To be defined", False),
        ("2040-01-01", "Unsupported", False),
        ("2040-01-01", "Best effort", False),
        ("01/01/2040", "2040-01-01", True),
        ("2040-01-01", "01/01/2040", True),
        ("01/01/2045", "01/01/2046", True),
        ("01/01/2045", "01/01/2044", False),
        ("01/01/2046", "01/01/2045", False),
        ("01/01/2044", "01/01/2045", True),
    ],
)
def test_is_supported(support, dependency_support, expected_result):
    assert _is_supported(support, dependency_support) == expected_result


@pytest.mark.parametrize(
    ("text", "expected_year", "expected_month", "expected_day", "expected_none"),
    [
        ("2024-06-01", 2024, 6, 1, False),
        ("01/06/2024", 2024, 6, 1, False),
        ("notadate", None, None, None, True),
    ],
)
def test_parse_support_date(text, expected_year, expected_month, expected_day, expected_none):
    result = _parse_support_date(text)
    if expected_none:
        assert result is None
    else:
        assert result is not None
        assert result.tzinfo is not None
        if expected_year is not None:
            assert result.year == expected_year
        if expected_month is not None:
            assert result.month == expected_month
        if expected_day is not None:
            assert result.day == expected_day


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("no support defined", _SupportCategory.NO_SUPPORT_DEFINED),
        ("unsupported", _SupportCategory.UNSUPPORTED),
        ("best effort", _SupportCategory.BEST_EFFORT),
        ("to be defined", _SupportCategory.TO_BE_DEFINED),
        ("2024-06-01", _SupportCategory.DATE),
        ("01/06/2024", _SupportCategory.DATE),
        ("other", _SupportCategory.UNKNOWN),
    ],
)
def test_support_category(value, expected):
    assert _support_category(value) == expected


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        # Category comparison
        ("unsupported", "best effort", -1),
        ("best effort", "unsupported", 1),
        ("best effort", "2024-06-01", -1),
        ("2024-06-01", "best effort", 1),
        ("to be defined", "2024-06-01", -1),
        ("2024-06-01", "to be defined", 1),
        ("other", "to be defined", -1),
        ("to be defined", "other", 1),
        # Date comparison
        ("2024-06-01", "2024-06-02", -1),
        ("2024-06-02", "2024-06-01", 1),
        ("2024-06-01", "2024-06-01", 0),
        # Same category
        ("unsupported", "unsupported", 0),
        ("best effort", "best effort", 0),
        ("to be defined", "to be defined", 0),
        ("other", "other", 0),
    ],
)
def test_support_cmp(a, b, expected):
    assert _support_cmp(a, b) == expected


def test_apply_additional_packages_least_support():
    # Setup transversal_status with two dependencies, one with better support than the other
    from github_app_geo_project.module.versions import (
        _UNSUPPORTED,
        _apply_additional_packages,
        _TransversalStatus,
        _TransversalStatusNameByDatasource,
        _TransversalStatusRepo,
        _TransversalStatusVersion,
    )

    # Existing repo with two versions, each with a different support
    transversal_status = _TransversalStatus(
        updated={
            "repo1": datetime.datetime.now(datetime.UTC),
        },
        repositories={
            "repo1": _TransversalStatusRepo(
                versions={
                    "1.0": _TransversalStatusVersion(
                        support={"type": "2024-06-01"},
                        names_by_datasource={
                            "pypi": _TransversalStatusNameByDatasource(names=["dep1"]),
                        },
                    ),
                    "2.0": _TransversalStatusVersion(
                        support={"type": "2023-06-01"},
                        names_by_datasource={
                            "pypi": _TransversalStatusNameByDatasource(names=["dep2"]),
                        },
                    ),
                }
            )
        },
    )

    # Additional package config with dependencies but no support set
    additional_packages = {
        "repo2": {
            "versions": {
                "main": {
                    "names_by_datasource": {
                        "pypi": {
                            "names": [
                                "repo2",
                            ],
                        },
                    },
                    "dependencies_by_datasource": {
                        "pypi": {
                            "versions_by_names": {
                                "dep1": {"versions": ["1.0"]},
                                "dep2": {"versions": ["2.0"]},
                            }
                        }
                    },
                }
            }
        }
    }

    class DummyContext:
        module_config = {"additional-packages": additional_packages}

    # Apply
    _apply_additional_packages(DummyContext(), transversal_status)

    # Should set support to the least support (most restrictive, i.e., "2023-06-01")
    repo2 = transversal_status.repositories["repo2"]
    main_version = repo2.versions["main"]
    assert main_version.support == "2023-06-01"
    assert repo2.names_index.model_dump() == {
        "pypi": {
            "repo2": {
                "main": "2023-06-01",
            },
        },
    }

    # Now test fallback to UNSUPPORTED if no dependency support found
    transversal_status.repositories.clear()
    additional_packages = {
        "repo3": {
            "versions": {
                "main": {
                    "dependencies_by_datasource": {
                        "pypi": {
                            "versions_by_names": {
                                "dep3": {"versions": ["3.0"]},
                            }
                        }
                    }
                }
            }
        }
    }
    DummyContext.module_config = {"additional-packages": additional_packages}
    _apply_additional_packages(DummyContext(), transversal_status)
    repo3 = transversal_status.repositories["repo3"]
    main_version = repo3.versions["main"]
    assert main_version.support == _UNSUPPORTED
