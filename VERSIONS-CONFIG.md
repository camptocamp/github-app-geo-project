# Versions configuration

## Properties

- <a id="properties/additional-packages"></a>**`additional-packages`** _(object)_: The additional packages to be added to the versions.
- <a id="properties/external-packages"></a>**`external-packages`** _(array)_

  - <a id="properties/external-packages/items"></a>**Items** _(object)_: Cannot contain additional properties.
    - <a id="properties/external-packages/items/properties/package"></a>**`package`** _(string, required)_: The name of the package from https://endoflife.date.
    - <a id="properties/external-packages/items/properties/datasource"></a>**`datasource`** _(string, required)_: The datasource of the dependencies.

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

- <a id="properties/repository-external"></a>**`repository-external`** _(string)_: The repository who manage the external packages.
- <a id="properties/package-extractor"></a>**`package-extractor`** _(object)_: The package extractor by datasource. Can contain additional properties.
  - <a id="properties/package-extractor/additionalProperties"></a>**Additional properties** _(object)_: The package extractor by package name. Can contain additional properties.
    - <a id="properties/package-extractor/additionalProperties/additionalProperties"></a>**Additional properties** _(array)_
      - <a id="properties/package-extractor/additionalProperties/additionalProperties/items"></a>**Items** _(object)_: Cannot contain additional properties.
        - <a id="properties/package-extractor/additionalProperties/additionalProperties/items/properties/version-extractor"></a>**`version-extractor`** _(string)_: The regular expression used to extract value from the package version.
        - <a id="properties/package-extractor/additionalProperties/additionalProperties/items/properties/datasource"></a>**`datasource`** _(string)_: The type of datasource.
        - <a id="properties/package-extractor/additionalProperties/additionalProperties/items/properties/requires"></a>**`requires`** _(array)_: The list of the required values to do the correspondence.
          - <a id="properties/package-extractor/additionalProperties/additionalProperties/items/properties/requires/items"></a>**Items** _(string)_
        - <a id="properties/package-extractor/additionalProperties/additionalProperties/items/properties/package"></a>**`package`** _(string, required)_: The name of the package that can be build from the extracted values.
        - <a id="properties/package-extractor/additionalProperties/additionalProperties/items/properties/version"></a>**`version`** _(string, required)_: The version of the package that can be build from the extracted values.
- <a id="properties/version-mapping"></a>**`version-mapping`** _(object)_: Mapping of version to the branch name. Can contain additional properties. Default: `{}`.
  - <a id="properties/version-mapping/additionalProperties"></a>**Additional properties** _(string)_
