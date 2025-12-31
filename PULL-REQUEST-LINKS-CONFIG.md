# Pull request add links configuration

## Properties

- <a id="properties/branch-patterns"></a>**`branch-patterns`** _(array)_: List of regular expressions used to get parameters form the branch names. Default: `["^(?P<project>[A-Z]{3,6})-(?P<issue>[0-9]+)-.*$", "^(?P<project>[a-z]{3,6})-(?P<issue>[0-9]+)-.*$", "^.*-(?P<project>[A-Z]{3,6})-(?P<issue>[0-9]+)$", "^.*-(?P<project>[a-z]{3,6})-(?P<issue>[0-9]+)$"]`.
  - <a id="properties/branch-patterns/items"></a>**Items** _(string)_
- <a id="properties/blacklist"></a>**`blacklist`** _(object)_: List of regular expressions used to exclude some parameters values. Can contain additional properties.
  - <a id="properties/blacklist/additionalProperties"></a>**Additional properties** _(array)_
    - <a id="properties/blacklist/additionalProperties/items"></a>**Items** _(string)_
- <a id="properties/uppercase"></a>**`uppercase`** _(array)_: List of parameters to convert to uppercase.
  - <a id="properties/uppercase/items"></a>**Items** _(string)_
- <a id="properties/lowercase"></a>**`lowercase`** _(array)_: List of parameters to convert to lowercase.
  - <a id="properties/lowercase/items"></a>**Items** _(string)_
- <a id="properties/content"></a>**`content`** _(array)_: List of elements to add to the pull request.
  - <a id="properties/content/items"></a>**Items** _(object)_: Cannot contain additional properties.
    - <a id="properties/content/items/properties/text"></a>**`text`** _(string)_: Default: `""`.
    - <a id="properties/content/items/properties/url"></a>**`url`** _(string)_
    - <a id="properties/content/items/properties/requires"></a>**`requires`** _(array)_
      - <a id="properties/content/items/properties/requires/items"></a>**Items** _(string)_
