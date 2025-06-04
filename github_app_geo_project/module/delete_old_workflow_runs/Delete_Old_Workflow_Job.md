Deletes the old workflow jobs.

### Functionality Details

This module automates the cleanup of old workflow runs in a repository. It helps maintain a tidy and efficient CI/CD environment by removing outdated workflow jobs that are no longer needed.

- **Workflow Run Deletion**: Identifies and deletes workflow runs based on age or other criteria.
- **Retention Policy**: Ensures that only workflow runs meeting the specified conditions are deleted, preserving important historical data.

### Configuration Options

You can configure the delete old workflow jobs module using the `.github/ghci.yaml` file.

[Configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/DELETE-OLD-WORKFLOW-RUN-CONFIG.md).
