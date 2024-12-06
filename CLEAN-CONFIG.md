# Clean modules configuration

## Properties

- **`clean`** _(object)_: Cannot contain additional properties.
  - **`docker`** _(boolean)_: Clean the docker images made from feature branches and pull requests. Default: `true`.
  - **`git`** _(array)_
    - **Items** _(object)_: Clean a folder from a branch. Cannot contain additional properties.
      - **`on-type`** _(string)_: feature_branch, pull_request or all. Must be one of: `["feature_branch", "pull_request", "all"]`. Default: `"all"`.
      - **`branch`** _(string)_: The branch on witch one the folder will be cleaned. Default: `"gh-pages"`.
      - **`folder`** _(string)_: The folder to be cleaned, can contains {name}, that will be replaced with the branch name or pull request number. Default: `"{name}"`.
