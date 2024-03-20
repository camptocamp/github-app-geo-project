"""Main acceptance test for the application."""

import os

import c2cwsgiutils.acceptance.image
import requests


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
                    }
                },
            }
        },
        "properties": {
            "profile": {
                "type": "string",
                "title": "Profile",
                "description": "The profile to use for the project",
            },
            "test": {
                "type": "object",
                "title": "Example",
                "description": "An example of a module properties",
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
        height=1500,
        result_folder="/results",
        expected_filename=os.path.join(os.path.dirname(__file__), "home.expected.png"),
    )


def test_project() -> None:
    """Tests that the home page can be loaded."""
    c2cwsgiutils.acceptance.image.check_screenshot(
        "http://application:8080/project/camptocamp/test",
        media=[
            {"name": "prefers-color-scheme", "value": "dark"},
        ],
        width=900,
        height=500,
        result_folder="/results",
        expected_filename=os.path.join(os.path.dirname(__file__), "project.expected.png"),
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
        expected_filename=os.path.join(os.path.dirname(__file__), "welcome.expected.png"),
    )
