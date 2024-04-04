from github_app_geo_project.module.audit import _format_issue_data, _parse_issue_data


def test_parse_issue_data() -> None:
    issue_data = "\n".join(
        [
            "",
            "### Key1",
            "Value1",
            "Value2",
            "",
            "### Key2",
            "Value3",
            "",
        ]
    )
    expected_result = {"Key1": ["Value1", "Value2"], "Key2": ["Value3"]}
    assert _parse_issue_data(issue_data) == expected_result


def test_format_issue_data() -> None:
    issue_data = {"Key1": ["Value1", "Value2"], "Key2": ["Value3"]}
    expected_result = "\n".join(
        [
            "",
            "### Key1",
            "Value1",
            "Value2",
            "",
            "### Key2",
            "Value3",
            "",
        ]
    )
    assert _format_issue_data(issue_data) == expected_result
