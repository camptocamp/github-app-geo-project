# Clean modules configuration

## Properties

- <a id="properties/clean"></a>**`clean`** _(object)_: Cannot contain additional properties.
  - <a id="properties/clean/properties/docker"></a>**`docker`** _(boolean)_: Clean the docker images made from feature branches and pull requests. Default: `true`.
  - <a id="properties/clean/properties/git"></a>**`git`** _(array)_
    - <a id="properties/clean/properties/git/items"></a>**Items** _(object)_: Clean a folder from a branch. Cannot contain additional properties.
      - <a id="properties/clean/properties/git/items/properties/on-type"></a>**`on-type`** _(string)_: feature_branch, pull_request or all. Must be one of: "feature_branch", "pull_request", or "all". Default: `"all"`.
      - <a id="properties/clean/properties/git/items/properties/branch"></a>**`branch`** _(string)_: The branch on witch one the folder will be cleaned. Default: `"gh-pages"`.
      - <a id="properties/clean/properties/git/items/properties/folder"></a>**`folder`** _(string)_: The folder to be cleaned, can contains {name}, that will be replaced with the branch name or pull request number. Default: `"{name}"`.
      - <a id="properties/clean/properties/git/items/properties/amend"></a>**`amend`** _(boolean)_: If true, the commit will be amended instead of creating a new one. Default: `false`.
