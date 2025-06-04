Add some link (and possibly test) in the pull request body.

In the `text` and `url` we can use the parameters or the regular expressions present in `branch-patterns` and also
`pull_request_number` and `head_branch`

Example:

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

[Configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/PULL-REQUEST-LINKS-CONFIG.md).
