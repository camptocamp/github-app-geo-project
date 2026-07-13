"""Tests for the views."""

import pytest

from github_app_geo_project.views.schema import schema_view


@pytest.mark.asyncio
async def test_schema_view_returns_dict() -> None:
    """schema_view should return a dict with expected keys."""
    result = await schema_view()
    assert isinstance(result, dict)
    assert "$schema" in result
    assert "$id" in result
    assert "properties" in result
    assert "$defs" in result
