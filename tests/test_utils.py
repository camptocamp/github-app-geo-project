from github_app_geo_project import utils


def test_get_dashboard_issue_module() -> None:
    text = "Some text\n<!-- START module1 -->\n## Title\n\nContent\n<!-- END module1 -->\nOther text"
    current_module = "module1"
    result = utils.get_dashboard_issue_module(text, current_module)
    assert result == "Content"


def test_update_dashboard_issue_module() -> None:
    text = "Some text\n<!-- START module1 -->\n## Title\n\nContent\n<!-- END module1 -->\nOther text"
    module_name = "module1"
    current_module = type("Module", (object,), {"title": lambda _: "New Title"})()
    data = "New Content"
    result = utils.update_dashboard_issue_module(text, module_name, current_module, data)
    expected = (
        "Some text\n<!-- START module1 -->\n## New Title\n\nNew Content\n<!-- END module1 -->\nOther text"
    )
    assert result == expected


def test_merge_css_blocks_no_duplicates() -> None:
    """merge_css_blocks should merge rules with the same selector."""
    result = utils.merge_css_blocks([".a { color: red; }", ".a { font-weight: bold; }"])
    assert "color: red" in result
    assert "font-weight: bold" in result
    assert result.count(".a") == 1


def test_merge_css_blocks_no_duplicates_value() -> None:
    """merge_css_blocks should deduplicate identical rules."""
    result = utils.merge_css_blocks([".a { color: red; }", ".a { color: red; }"])
    assert result.count("color: red") == 1
    assert result.count(".a") == 1


def test_merge_css_blocks_multiple_selectors() -> None:
    """merge_css_blocks should handle multiple different selectors."""
    result = utils.merge_css_blocks([".a { color: red; }", ".b { color: blue; }"])
    assert ".a" in result
    assert ".b" in result
    assert "color: red" in result
    assert "color: blue" in result


def test_merge_css_blocks_backgrounds_priority() -> None:
    """merge_css_blocks should keep the last value for same property."""
    result = utils.merge_css_blocks([".a { color: red; }", ".a { color: blue; }"])
    assert "color: red" not in result
    assert "color: blue" in result
