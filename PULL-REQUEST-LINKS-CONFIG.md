# Pull request add links configuration

## Properties

- <a id="properties/branch-patterns"></a>**`branch-patterns`** *(array)*: List of regular expressions used to get parameters form the branch names. Default: `["^(?P<project>[A-Z]{3,6})-(?P<issue>[0-9]+)-.*$", "^(?P<project>[a-z]{3,6})-(?P<issue>[0-9]+)-.*$", "^.*-(?P<project>[A-Z]{3,6})-(?P<issue>[0-9]+)$", "^.*-(?P<project>[a-z]{3,6})-(?P<issue>[0-9]+)$"]`.
  - <a id="properties/branch-patterns/items"></a>**Items** *(string)*
- <a id="properties/blacklist"></a>**`blacklist`** *(object)*: List of regular expressions used to exclude some parameters values. Can contain additional properties.
  - <a id="properties/blacklist/additionalProperties"></a>**Additional properties** *(array)*
    - <a id="properties/blacklist/additionalProperties/items"></a>**Items** *(string)*
- <a id="properties/uppercase"></a>**`uppercase`** *(array)*: List of parameters to convert to uppercase.
  - <a id="properties/uppercase/items"></a>**Items** *(string)*
- <a id="properties/lowercase"></a>**`lowercase`** *(array)*: List of parameters to convert to lowercase.
  - <a id="properties/lowercase/items"></a>**Items** *(string)*
- <a id="properties/content"></a>**`content`** *(array)*: List of elements to add to the pull request.
  - <a id="properties/content/items"></a>**Items** *(object)*: Cannot contain additional properties.
    - <a id="properties/content/items/properties/text"></a>**`text`** *(string)*: Default: `""`.
    - <a id="properties/content/items/properties/url"></a>**`url`** *(string)*
    - <a id="properties/content/items/properties/requires"></a>**`requires`** *(array)*
      - <a id="properties/content/items/properties/requires/items"></a>**Items** *(string)*
