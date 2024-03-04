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
