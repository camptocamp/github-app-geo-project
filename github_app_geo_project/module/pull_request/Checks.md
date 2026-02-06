Validate the pull request title and body, commits title and body about typo, spelling,
merge commit, ...

### Functionality Details

This module ensures that pull requests meet predefined standards for quality and consistency. It validates:

- **Pull Request Title and Body**: Checks for typos, spelling errors, and adherence to formatting guidelines.
- **Commit Messages**: Ensures commit titles and bodies are free of typos, spelling errors, and unnecessary merge commits.
- **Merge Commit Detection**: Flags merge commits that may not be appropriate for the pull request.

### When is it triggered?

The module is triggered during pull request events, such as creation, updates, or labeling. It helps maintain a clean and professional repository by enforcing standards.

### Configuration Options

You can configure the pull request checks module using the `.github/ghci.yaml` file.

[Configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/PULL-REQUEST-CHECKS-CONFIG.md).

### Usage Notes

- Ensure the module is configured to match your repository's standards for pull requests and commits.
- Use the configuration options to tailor the checks to your project's needs.
- Review flagged issues to ensure they are valid before taking action.
