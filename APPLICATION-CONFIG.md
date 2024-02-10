# GitHub application project configuration

## Properties

- **`default-profile`** _(string)_: The profile name used by default.
- **`applications`** _(object)_: The applications configuration. Can contain additional properties.
  - **Additional properties** _(object)_: The application configuration. Cannot contain additional properties.
    - **`if`** _(string)_: The application ID.
    - **`private-key`** _(string)_: The private key used to authenticate the application.
- **`profiley`** _(object)_: The profiles configuration. Can contain additional properties.
  - **Additional properties**: Refer to _[#/definitions/project](#definitions/project)_.

## Definitions

- <a id="definitions/project"></a>**`project`** _(object)_: The project configuration. Cannot contain additional properties.
  - **`profile`** _(string)_: The profile to use for the project.
  - **`changelog`** _(object)_: The changelog generation configuration. Cannot contain additional properties.
    - **`enabled`** _(boolean)_: Enable the changelog generation. Default: `true`.
