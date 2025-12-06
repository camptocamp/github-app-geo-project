# Auto pull request modules configuration base

## Properties

- <a id="properties/auto-review"></a>**`auto-review`**: Refer to _[#/definitions/auto](#definitions/auto)_.
- <a id="properties/auto-merge"></a>**`auto-merge`**: Refer to _[#/definitions/auto](#definitions/auto)_.
- <a id="properties/auto-close"></a>**`auto-close`**: Refer to _[#/definitions/auto](#definitions/auto)_.

## Definitions

- <a id="definitions/auto"></a>**`auto`** _(object)_: auto pull request configuration. Cannot contain additional properties.
  - <a id="definitions/auto/properties/conditions"></a>**`conditions`** _(array)_
    - <a id="definitions/auto/properties/conditions/items"></a>**Items** _(object)_: Cannot contain additional properties.
      - <a id="definitions/auto/properties/conditions/items/properties/author"></a>**`author`** _(string)_: The author of the pull request.
      - <a id="definitions/auto/properties/conditions/items/properties/branch"></a>**`branch`** _(string)_: Regex to match the branch of the pull request.
      - <a id="definitions/auto/properties/conditions/items/properties/title"></a>**`title`** _(string)_: Regex to match the title of the pull request.
