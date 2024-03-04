# Auto pull request modules configuration base

## Properties

- **`auto-review`**: Refer to _[#/definitions/auto](#definitions/auto)_.
- **`auto-merge`**: Refer to _[#/definitions/auto](#definitions/auto)_.
- **`auto-close`**: Refer to _[#/definitions/auto](#definitions/auto)_.

## Definitions

- <a id="definitions/auto"></a>**`auto`** _(object)_: auto pull request configuration. Cannot contain additional properties.
  - **`conditions`** _(array)_
    - **Items** _(object)_: Cannot contain additional properties.
      - **`author`** _(string)_: The author of the pull request.
      - **`branch`** _(string)_: Regex to match the branch of the pull request.
      - **`title`** _(string)_: Regex to match the title of the pull request.
