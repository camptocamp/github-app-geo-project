# Outdated Comments Module

This module automatically marks previous comments from specific authors as outdated when new comments are submitted. It's particularly useful for managing automated comments from tools like GitHub Copilot and other bots.

## How It Works

When a pull request review is submitted, this module:

1. Checks if the comment's author is in the configured list of authors to monitor
2. Finds all previous comments from the same (or equivalent) author on that pull request
3. Marks those previous comments as outdated (minimizes them with “outdated” classification)
4. Preserves only the most recent comment

This helps keep pull requests clean by minimizing old automated comments that may no longer be relevant.

## Configuration

Configure this module in your `.github/github-app-geo-project.yaml` file:

```yaml
outdated_comments:
  authors:
    - - author1
      - equivalent_author1
    - - author2
      - equivalent_author2
```

Each entry in the `authors` list is an array of equivalent author logins. This is necessary because some systems (like GitHub Copilot) use different logins for the event sender versus the comment author.

### GitHub Copilot Configuration

For GitHub Copilot, use the following configuration because the author login is not the same in the event as in the messages:

```yaml
outdated_comments:
  authors:
    - - Copilot
      - copilot-pull-request-reviewer[bot]
```

## Permissions Required

This module requires the following GitHub permissions:

- `pull_requests: write` - To modify comment visibility status
- Events: `pull_request_review` - To trigger on new comments

## Example Usage

When configured properly, this module will automatically minimize previous Copilot comments when a new one is added, keeping only the most recent feedback visible by default.

## Troubleshooting

If comments aren't being marked as outdated:

1. Verify the author names in your configuration match exactly with what appears in GitHub
2. Check that you've included all variant names an author might use
3. Confirm the GitHub App has proper permissions set
