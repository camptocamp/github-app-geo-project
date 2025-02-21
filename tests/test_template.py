from datetime import UTC, datetime, timedelta

from github_app_geo_project.templates import markdown, pprint_date, pprint_duration, sanitizer


def test_sanitizer() -> None:
    # Test input with HTML tags
    input_text = "<p>Hello, <strong>world!</strong></p>"
    expected_output = "<p>Hello, <strong>world!</strong></p>"
    assert sanitizer(input_text) == expected_output

    # Test input without HTML tags
    input_text = "Hello, world!"
    expected_output = "Hello, world!"
    assert sanitizer(input_text) == expected_output

    # Test input with script
    input_text = "<script>alert('Hello, world!');</script>"
    expected_output = ""
    assert sanitizer(input_text) == expected_output


def test_markdown() -> None:
    # Test input with HTML tags
    input_text = "# Hello, world!"
    expected_output = "<h1>Hello, world!</h1>"
    assert markdown(input_text) == expected_output


def test_pprint_date() -> None:
    # Test case when date_str is "None"
    date_str = "None"
    expected_output = "-"
    assert pprint_date(date_str) == expected_output

    # Test case when date is now
    now = datetime.now(UTC)
    date_str = now.isoformat()
    expected_output = '<span title="{}">now</span>'.format(now.strftime("%Y-%m-%d %H:%M:%S"))
    assert pprint_date(date_str) == expected_output

    # Test case when date is within 1 minute
    date = now - timedelta(seconds=30)
    date_str = date.isoformat()
    expected_output = '<span title="{}">30 seconds ago</span>'.format(date.strftime("%Y-%m-%d %H:%M:%S"))
    assert pprint_date(date_str) == expected_output

    # Test case when date is within 1 hour
    date = now - timedelta(minutes=45)
    date_str = date.isoformat()
    expected_output = '<span title="{}">45 minutes ago</span>'.format(date.strftime("%Y-%m-%d %H:%M:%S"))
    assert pprint_date(date_str) == expected_output

    # Test case when date is within 1 day
    date = now - timedelta(hours=12)
    date_str = date.isoformat()
    expected_output = '<span title="{}">12 hours ago</span>'.format(date.strftime("%Y-%m-%d %H:%M:%S"))
    assert pprint_date(date_str) == expected_output

    # Test case when date is within 30 days
    date = now - timedelta(days=15)
    date_str = date.isoformat()
    expected_output = '<span title="{}">15 days ago</span>'.format(date.strftime("%Y-%m-%d %H:%M:%S"))
    assert pprint_date(date_str) == expected_output

    # Test case when date is more than 30 days ago
    date = now - timedelta(days=60)
    date_str = date.isoformat()
    expected_output = '<span title="{}">{}</span>'.format(
        date.strftime("%Y-%m-%d %H:%M:%S"),
        date.strftime("%Y-%m-%d"),
    )
    assert pprint_date(date_str) == expected_output


def test_pprint_duration() -> None:
    # Test case when duration_str is "None"
    duration_str = "None"
    expected_output = "-"
    assert pprint_duration(duration_str) == expected_output

    # Test case when duration is less than 1 minute
    duration_str = "00:00:30.500"
    expected_output = "30 seconds"
    assert pprint_duration(duration_str) == expected_output
    duration_str = "00:00:30.501"
    expected_output = "31 seconds"
    assert pprint_duration(duration_str) == expected_output

    # Test case when duration is less than 1 hour
    duration_str = "00:45:30.500"
    expected_output = "46 minutes"
    assert pprint_duration(duration_str) == expected_output

    # Test case when duration is less than 1 day
    duration_str = "12:30:30.500"
    expected_output = "13 hours"
    assert pprint_duration(duration_str) == expected_output

    # Test case when duration is more than 1 day
    duration_str = "2 days, 12:30:30"
    expected_output = "3 days"
    assert pprint_duration(duration_str) == expected_output

    duration_str = "1 day, 1:30:30"
    expected_output = "1 day"
    assert pprint_duration(duration_str) == expected_output

    # test negative duration
    duration_str = "-1 day, 3:37:51.815113"
    expected_output = "-20 hours"
    assert pprint_duration(duration_str) == expected_output

    duration_str = "-2 day, 18:37:51.815113"
    expected_output = "-1 day"
    assert pprint_duration(duration_str) == expected_output

    duration_str = "-2 day, 3:37:51.815113"
    expected_output = "-2 days"
    assert pprint_duration(duration_str) == expected_output
