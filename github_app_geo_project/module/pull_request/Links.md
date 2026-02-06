Add some link (and possibly test) in the pull request body.

### Functionality Details

This module automates the addition of links to the pull request body based on branch patterns and other parameters. It can:

- Generate links using predefined templates and parameters extracted from branch names.
- Optionally test the generated links to ensure they are valid and accessible.

### Configuration Options

You can configure the pull request links module using the `.github/ghci.yaml` file or a similar configuration file. Example options include:

- `branch-patterns`: A list of regular expressions to extract parameters from branch names.
- `uppercase`: A list of parameters to convert to uppercase.
- `content`: A list of link templates with text, URL, and required parameters.

### Example Configuration

```yaml
branch-patterns:
  - ^(?P<project>[A-Z]{3,6})-(?P<issue>[0-9]+)-.*$
  - ^(?P<project>[a-z]{3,6})-(?P<issue>[0-9]+)-.*$
  - ^.*-(?P<project>[A-Z]{3,6})-(?P<issue>[0-9]+)$
  - ^.*-(?P<project>[a-z]{3,6})-(?P<issue>[0-9]+)$
uppercase:
  - project
content:
  - text: JIRA issue: {project}-{issue}
    url: https://jira.camptocamp.com/browse/{project}-{issue}
    requires:
      - project
      - issue
```

### Usage Notes

- Ensure the branch patterns are correctly defined to extract the required parameters.
- Use the `requires` field in link templates to validate the presence of necessary parameters.
- Test the generated links to ensure they point to valid resources.

[Configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/PULL-REQUEST-LINKS-CONFIG.md).
