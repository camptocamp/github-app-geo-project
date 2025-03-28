# Versions configuration

## Properties

- **`additional-packages`** _(object)_: The additional packages to be added to the versions.
- **`external-packages`** _(array)_

  - **Items** _(object)_: Cannot contain additional properties.
    - **`package`** _(string, required)_: The name of the package from https://endoflife.date.
    - **`datasource`** _(string, required)_: The datasource of the dependencies.

  Examples:

  ```json
  {
    "package": "python",
    "datasource": "pypi"
  }
  ```

  ```json
  {
    "package": "ubuntu",
    "datasource": "docker"
  }
  ```

  ```json
  {
    "package": "debian",
    "datasource": "docker"
  }
  ```

  ```json
  {
    "package": "node",
    "datasource": "node-version"
  }
  ```

  ```json
  {
    "package": "java",
    "datasource": "package"
  }
  ```

  ```json
  {
    "package": "redis",
    "datasource": "package"
  }
  ```

  ```json
  {
    "package": "haproxy",
    "datasource": "package"
  }
  ```

  ```json
  {
    "package": "kubernetes",
    "datasource": "package"
  }
  ```

  ```json
  {
    "package": "tomcat",
    "datasource": "package"
  }
  ```

  ```json
  {
    "package": "postgres",
    "datasource": "package"
  }
  ```

- **`repository-external`** _(string)_: The repository who manage the external packages.
- **`package-extractor`** _(object)_: The package extractor by datasource. Can contain additional properties.
  - **Additional properties** _(object)_: The package extractor by package name. Can contain additional properties.
    - **Additional properties** _(array)_
      - **Items** _(object)_: Cannot contain additional properties.
        - **`version-extractor`** _(string)_: The regular expression used to extract value from the package version.
        - **`datasource`** _(string)_: The type of datasource.
        - **`requires`** _(array)_: The list of the required values to do the correspondence.
          - **Items** _(string)_
        - **`package`** _(string, required)_: The name of the package that can be build from the extracted values.
        - **`version`** _(string, required)_: The version of the package that can be build from the extracted values.
- **`version-mapping`** _(object)_: Mapping of version to the branch name. Can contain additional properties. Default: `{}`.
  - **Additional properties** _(string)_
