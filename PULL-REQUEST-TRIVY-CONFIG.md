# Pull request trivy configuration

## Properties

- <a id="properties/scanners"></a>**`scanners`** *(array)*: The Trivy scanners run on the pull request with the `trivy fs` command. Default: `["secret", "misconfig"]`.
  - <a id="properties/scanners/items"></a>**Items** *(string)*
- <a id="properties/severity"></a>**`severity`** *(string)*: The minimum severities reported by the Trivy scans, as a comma separated list. Default: `"HIGH,CRITICAL"`.
- <a id="properties/ignore-file"></a>**`ignore-file`** *(string)*: The path of the Trivy ignore file in the repository, the `--ignorefile` argument is added only if the file exists. Default: `".github/.trivyignore.yaml"`.
- <a id="properties/config-targets"></a>**`config-targets`** *(array)*: The files scanned with the `trivy config` command, the command is run only for the files that exist in the repository. Default: `["Dockerfile"]`.
  - <a id="properties/config-targets/items"></a>**Items** *(string)*
