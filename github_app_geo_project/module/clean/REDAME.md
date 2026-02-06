Module used to clean artifacts related to a feature branch. Concerned items:

- Docker image.
- Folder in gh-pages branch.

### Functionality Details

This module is responsible for cleaning up resources that are created for feature branches, ensuring that obsolete or unnecessary artifacts do not accumulate in the project infrastructure. The main items targeted for cleanup are:

- **Docker images**: Removes Docker images that were built for feature branches and are no longer needed.
- **gh-pages folders**: Deletes folders in the `gh-pages` branch that correspond to feature branches, freeing up space and keeping the documentation or static site clean.

### When is it triggered?

The clean module is typically triggered when a feature branch is deleted or merged, or as part of a scheduled maintenance process. This helps maintain a tidy project environment and reduces storage costs.

### Configuration Options

You can configure the clean module using the `.github/ghci.yaml` file.

[Configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/CLEAN-CONFIG.md).

### Usage Notes

- Make sure you have the necessary permissions to delete Docker images and update the `gh-pages` branch.
- Review the configuration to avoid accidental deletion of important resources.
