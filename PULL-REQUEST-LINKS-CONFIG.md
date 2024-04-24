# Pull request add links configuration

## Properties

- **`branch-patterns`** _(array)_: List of regular expressions used to get parameters form the branch names. Default: `["^(?P<project>[A-Z]{3,6})-(?P<issue>[0-9]+)-.*$", "^(?P<project>[a-z]{3,6})-(?P<issue>[0-9]+)-.*$", "^.*-(?P<project>[A-Z]{3,6})-(?P<issue>[0-9]+)$", "^.*-(?P<project>[a-z]{3,6})-(?P<issue>[0-9]+)$"]`.
  - **Items** _(string)_
- **`blacklist`** _(object)_: List of regular expressions used to exclude some parameters values. Can contain additional properties.
  - **Additional properties** _(array)_
    - **Items** _(string)_
- **`uppercase`** _(array)_: List of parameters to convert to uppercase.
  - **Items** _(string)_
- **`lowercase`** _(array)_: List of parameters to convert to lowercase.
  - **Items** _(string)_
- **`content`** _(array)_: List of elements to add to the pull request.
  - **Items** _(object)_: Cannot contain additional properties.
    - **`text`** _(string)_: Default: `""`.
    - **`url`** _(string)_
    - **`requires`** _(array)_
      - **Items** _(string)_
