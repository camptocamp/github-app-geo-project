The auto modules will do some simple operations on modules:

- Auto review: add a positive review on the pull request (doesn't work as expected because the application cannot be set as an official reviewer)
- Auto merge: activate the auto-merge option of a pull request, even if it is preferable that the modules themselves activate this option.
- Auto close: close the pull request, used to automatically close unwanted pull requests created by applications like pre-commit.

The module has a `condition` option to select the affected pull requests.

### Functionality Details

- **Auto Review**: Automatically adds a positive review to eligible pull requests. This is useful for automating approval workflows, but note that the GitHub application cannot be set as an official reviewer, so this may not trigger all required checks.
- **Auto Merge**: Enables the auto-merge option on pull requests that meet the specified conditions. This helps streamline the merging process for trusted or routine changes.
- **Auto Close**: Closes pull requests that match certain criteria, such as those created by automated tools or that are no longer needed.

### When is it triggered?

The module is typically triggered by pull request events (opened, updated, labeled, etc.) or as part of scheduled automation runs.

### Configuration Options

You can configure the module using the `.github/ghci.yaml` file.

[Configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/AUTO-CONFIG.md).

### Example Configuration

```yaml
auto-review:
  enabled: true
  condition:
    author: 'dependabot[bot]'
```

### Usage Notes

- Ensure the GitHub App has the necessary permissions to review, merge, and close pull requests.
- Use the `condition` option to avoid affecting unintended pull requests.
- The auto review feature may not satisfy all required review checks due to GitHub limitations.
