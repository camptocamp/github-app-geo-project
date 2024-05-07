from unittest.mock import Mock

import responses

from github_app_geo_project.module.versions import (
    ProcessOutput,
    Versions,
    _canonical_minor_version,
    _EventData,
    _ReverseDependencies,
    _ReverseDependency,
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


async def test_process_step_1() -> None:
    versions = Versions()
    context = Mock()
    context.event_data = _EventData(step=1)
    context.transversal_status = _TransversalStatus()

    output = await versions.process(context)
    assert len(output.actions) == 1
    assert output.actions[0].data == {"step": 2}
    assert isinstance(output.transversal_status, dict)
    assert output.transversal_status == {}


async def test_process_step_2() -> None:
    versions = Versions()
    context = Mock()
    context.event_data = _EventData(step=2)
    context.transversal_status = _TransversalStatus()
    #    "github_project": {"owner": "owner", "repository": "repo", "github": "github"},
    output = await versions.process(context)
    assert isinstance(output.transversal_status, dict)
    assert output.transversal_status == {
        "updated": {"branch": "branch"},
        "repositories": {"branch": "branch"},
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
    assert output.data["reverse_dependencies"] == _ReverseDependencies(
        by_branch={
            "1.0": [
                _ReverseDependency(
                    name="other_package",
                    status_by_version="2.0.1",
                    support="Best effort",
                    color="--bs-danger",
                    repo="camptocamp/other",
                )
            ]
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
def test__update_upstream_versions():
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
