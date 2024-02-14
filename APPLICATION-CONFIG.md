# GitHub application project configuration

## Properties

- **`title`** _(string)_: The title of the project.
- **`description`** _(string)_: The description of the project.
- **`documentation-url`** _(string)_: The URL of the documentation.
- **`start-url`** _(string)_: The URL of the start page.
- **`default-profile`** _(string)_: The profile name used by default.
- **`profiles`** _(object)_: The profiles configuration. Can contain additional properties.
  - **Additional properties** _(object)_
    - **All of**
      - - **`inherits`** _(string)_: The profile to inherit from.
      - : Refer to _[#/$defs/project-configuration](#%24defs/project-configuration)_.

## Definitions

- <a id="%24defs/project-configuration"></a>**`project-configuration`** _(object)_: Can contain additional properties.
  - **Additional properties** _(object)_
    - **All of**
      - - **`enabled`** _(boolean)_: Enable the module.
        - **`application`** _(string)_: The GitHub application used by the module.
      - - **One of**
          - : The changelog generation configuration.
            - **`create-label`** _(boolean)_: Automatically create the labels used in the changelog configuration.
            - **`labels`** _(object)_: The labels configuration. Can contain additional properties.
              - **Additional properties** _(object)_: The label configuration.
                - **`description`** _(string)_: The description of the label.
                - **`color`** _(string)_: The color of the label.
            - **`sections`** _(array)_: The sections configuration.
              - **Items** _(object)_: The section configuration.
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
