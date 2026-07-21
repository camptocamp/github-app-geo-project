# Auto pull request modules configuration base

## Properties

- <a id="properties/auto-review"></a>**`auto-review`**: Refer to *[#/definitions/auto](#definitions/auto)*.
- <a id="properties/auto-merge"></a>**`auto-merge`**: Refer to *[#/definitions/auto](#definitions/auto)*.
- <a id="properties/auto-close"></a>**`auto-close`**: Refer to *[#/definitions/auto](#definitions/auto)*.
## Definitions

- <a id="definitions/auto"></a>**`auto`** *(object)*: auto pull request configuration. Cannot contain additional properties.
  - <a id="definitions/auto/properties/conditions"></a>**`conditions`** *(array)*
    - <a id="definitions/auto/properties/conditions/items"></a>**Items** *(object)*: Cannot contain additional properties.
      - <a id="definitions/auto/properties/conditions/items/properties/author"></a>**`author`** *(string)*: The author of the pull request.
      - <a id="definitions/auto/properties/conditions/items/properties/branch"></a>**`branch`** *(string)*: Regex to match the branch of the pull request.
      - <a id="definitions/auto/properties/conditions/items/properties/title"></a>**`title`** *(string)*: Regex to match the title of the pull request.
