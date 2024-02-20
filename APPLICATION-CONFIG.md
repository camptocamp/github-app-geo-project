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
  - **Additional properties**: Refer to _[#/$defs/module_configuration](#%24defs/module_configuration)_.
- <a id="%24defs/module_configuration"></a>**`module_configuration`** _(object)_
  - **`enabled`** _(boolean)_: Enable the module.
