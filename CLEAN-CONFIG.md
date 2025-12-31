# Clean modules configuration

## Properties

- <a id="properties/clean"></a>**`clean`** *(object)*: Cannot contain additional properties.
  - <a id="properties/clean/properties/docker"></a>**`docker`** *(boolean)*: Clean the docker images made from feature branches and pull requests. Default: `true`.
  - <a id="properties/clean/properties/git"></a>**`git`** *(array)*
    - <a id="properties/clean/properties/git/items"></a>**Items** *(object)*: Clean a folder from a branch. Cannot contain additional properties.
      - <a id="properties/clean/properties/git/items/properties/on-type"></a>**`on-type`** *(string)*: feature_branch, pull_request or all. Must be one of: "feature_branch", "pull_request", or "all". Default: `"all"`.
      - <a id="properties/clean/properties/git/items/properties/branch"></a>**`branch`** *(string)*: The branch on witch one the folder will be cleaned. Default: `"gh-pages"`.
      - <a id="properties/clean/properties/git/items/properties/folder"></a>**`folder`** *(string)*: The folder to be cleaned, can contains {name}, that will be replaced with the branch name or pull request number. Default: `"{name}"`.
      - <a id="properties/clean/properties/git/items/properties/amend"></a>**`amend`** *(boolean)*: If true, the commit will be amended instead of creating a new one. Default: `false`.
