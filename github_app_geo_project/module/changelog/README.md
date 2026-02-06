Generate the changelog and store it on the release.

### Functionality Details

This module automates the generation and management of changelogs for releases. It:

- **Changelog Generation**: Creates a changelog when a tag or release is created, depending on the `create-release` option.
- **Milestone Management**: Automatically creates a milestone and assigns pull requests to it.
- **Changelog Updates**: Updates the changelog if a closed pull request linked to a milestone is modified.
- **Section Configuration**: Allows customization of changelog sections using `sections` and `routing` options.

### Configuration Options

You can configure the changelog module using the `.github/ghci.yaml` file or a similar configuration file.

[Configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/CHANGELOG-CONFIG.md).

### Tips

To generate a changelog for an already existing release:

- Create a milestone with the same name as the tag (if not already existing).
- Assign the milestone to a pull request of the release.
- Edit this pull request.

### Usage Notes

- Ensure the `sections` and `routing` options are configured to match your project's labeling conventions.
- Regularly review the changelog to ensure it accurately reflects the changes in each release.
