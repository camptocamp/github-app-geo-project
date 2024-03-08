"""Main acceptance test for the application."""

import requests


def test_ok() -> None:
    """Tests that the application can be loaded."""
    response = requests.get("http://application:8080/", timeout=30)  # nosec
    assert response.status_code == 200
