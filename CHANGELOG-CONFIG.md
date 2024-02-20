# GitHub application project configuration

## Properties

- **`changelog`** _(object)_: The changelog generation configuration. Cannot contain additional properties.
  - **`create-label`** _(boolean)_: Automatically create the labels used in the changelog configuration.
  - **`labels`** _(object)_: The labels configuration. Can contain additional properties.
    - **Additional properties** _(object)_: The label configuration.
      - **`description`** _(string)_: The description of the label.
      - **`color`** _(string)_: The color of the label.
  - **`sections`** _(array)_: The sections configuration.
    - **Items** _(object)_: The section configuration.
      - **`name`** _(string)_: The name of the section.
      - **`title`** _(string)_: The title of the section.
      - **`description`** _(string)_: The description of the section.
  - **`default-section`** _(string)_: The default section for items.
  - **`routing`** _(array)_: The routing configuration.
    - **Items** _(object)_: The routing configuration.
      - **`section`** _(string)_: The section section affected to changelog items that match with the conditions.
      - **`condition`** _(object)_: The condition to match with the changelog items. Cannot contain additional properties.
        - **Any of**
          - - **`type`** _(string)_: The type of the condition.
            - **`value`** _(boolean)_: The value of the condition.
          - - **`type`** _(string)_: The type of the condition. Must be one of: `["and", "or"]`.
            - **`conditions`** _(array)_: The value of the conditions.
              - **Items**
          - - **`type`** _(string)_: The type of the condition. Must be one of: `["not"]`.
            - **`condition`**
          - - **`type`** _(string)_: The type of the condition.
            - **`value`** _(string)_: The value of the label.
          - - **`type`** _(string)_: The type of the condition.
            - **`regex`** _(array)_: The list of regex that all the files should match.
              - **Items** _(string)_: The regex that all the files should match.
          - - **`type`** _(string)_: The type of the condition.
            - **`value`** _(string)_: The value of the author.
          - - **`type`** _(string)_: The type of the condition.
            - **`regex`** _(string)_: The regex the the title should match.
          - - **`type`** _(string)_: The type of the condition.
            - **`regex`** _(string)_: The regex the the title should match.
