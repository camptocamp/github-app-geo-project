Module used to manage the backport of a pull request to another branch.

### Functionality Details

This module automates the process of backporting pull requests to stabilization or other branches. It:

- **Label Management**: Updates labels on pull requests based on the [`SECURITY.md`](https://github.com/camptocamp/c2cciutils/wiki/SECURITY.md) file.
- **Workflow Validation**: Fails workflows if the `BACKPORT_TODO` file exists, ensuring that unresolved backport tasks are addressed.
- **Backport Pull Request Creation**: Automatically creates backport pull requests to specified branches.

### Configuration Options

You can configure the backport module using the `.github/ghci.yaml` file or a similar configuration file. Example options include:

[Configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/BACKPORT-CONFIG.md).

### Usage Notes

- Ensure the `SECURITY.md` file is up-to-date to accurately reflect stabilization branches.
- Use the `BACKPORT_TODO` file to track unresolved backport tasks.
- Regularly review backport pull requests to ensure they meet project standards.
