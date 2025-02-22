"""Main acceptance test for the application."""

from pathlib import Path

import c2cwsgiutils.acceptance.image
import pytest
import requests
import yaml


def test_ok() -> None:
    """Tests that the application can be loaded."""
    response = requests.get("http://application:8080/", timeout=30)  # nosec
    assert response.status_code == 200


def test_schema() -> None:
    """Tests that the schema can be loaded."""
    response = requests.get("http://application:8080/schema.json", timeout=30)  # nosec
    assert response.status_code == 200
    assert response.json() == {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": "https://raw.githubusercontent.com/camptocamp/github-app-geo-project/github_app_geo_project/project-schema.json",
        "type": "object",
        "title": "GitHub application project configuration",
        "$defs": {
            "module-configuration": {
                "type": "object",
                "title": "Module configuration",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": "Enable the module",
                        "title": "Module enabled",
                        "default": True,
                    },
                },
            },
        },
        "properties": {
            "profile": {
                "type": "string",
                "title": "Profile",
                "description": "The profile to use for the project",
            },
            "test": {
                "type": "object",
                "title": "Test Module",
                "description": "This module is used to test the application",
                "allOf": [
                    {"$ref": "#/$defs/module-configuration"},
                    {"type": "object", "properties": {"test": {"type": "string"}}},
                ],
            },
        },
    }


def test_home() -> None:
    """Tests that the home page can be loaded."""
    c2cwsgiutils.acceptance.image.check_screenshot(
        "http://application:8080/",
        media=[
            {"name": "prefers-color-scheme", "value": "dark"},
        ],
        width=900,
        height=1900,
        result_folder="/results",
        expected_filename=str(Path(__file__).parent / "home.expected.png"),
        sleep=500,
    )


def test_project() -> None:
    """Tests that the home page can be loaded."""
    c2cwsgiutils.acceptance.image.check_screenshot(
        "http://application:8080/project/camptocamp/test",
        media=[
            {"name": "prefers-color-scheme", "value": "dark"},
        ],
        width=1000,
        height=900,
        result_folder="/results",
        expected_filename=str(Path(__file__).parent / "project.expected.png"),
        sleep=500,
    )


def test_welcome() -> None:
    """Tests that the home page can be loaded."""
    c2cwsgiutils.acceptance.image.check_screenshot(
        "http://application:8080/welcome?installation_id=1234&setup_action=install",
        media=[
            {"name": "prefers-color-scheme", "value": "dark"},
        ],
        width=900,
        height=500,
        result_folder="/results",
        expected_filename=str(Path(__file__).parent / "welcome.expected.png"),
        sleep=500,
    )


def test_transversal_dashboard() -> None:
    """Tests that the home page can be loaded."""
    c2cwsgiutils.acceptance.image.check_screenshot(
        "http://application:8080/dashboard/test",
        media=[
            {"name": "prefers-color-scheme", "value": "dark"},
        ],
        width=900,
        height=200,
        result_folder="/results",
        expected_filename=str(Path(__file__).parent / "dashboard.expected.png"),
        sleep=500,
    )


@pytest.mark.parametrize("log_type", ["success", "error", "log-multiline", "log-command", "log-json"])
def test_logs(log_type: str) -> None:
    """Tests the logs page."""
    with Path("/results/test-result.yaml").open(encoding="utf-8") as file:  # nosec
        result = yaml.load(file, Loader=yaml.SafeLoader)

    c2cwsgiutils.acceptance.image.check_screenshot(
        f"http://application:8080/logs/{result[f'{log_type}-job-id']}",
        media=[
            {"name": "prefers-color-scheme", "value": "dark"},
        ],
        width=900,
        height=900,
        result_folder="/results",
        expected_filename=str(Path(__file__).parent / f"logs-{log_type}.expected.png"),
        sleep=500,
    )


@pytest.mark.parametrize("output_type", ["multi-line", "error"])
def test_output(output_type: str) -> None:
    """Tests the output page."""
    with Path("/results/test-result.yaml").open(encoding="utf-8") as file:  # nosec
        result = yaml.load(file, Loader=yaml.SafeLoader)

    c2cwsgiutils.acceptance.image.check_screenshot(
        f"http://application:8080/output/{result[f'output-{output_type}-id']}",
        media=[
            {"name": "prefers-color-scheme", "value": "dark"},
        ],
        width=900,
        height=200,
        result_folder="/results",
        expected_filename=str(Path(__file__).parent / f"output-{output_type}.expected.png"),
        sleep=500,
    )
