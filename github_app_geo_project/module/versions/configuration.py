"""
Automatically generated file from a JSON schema.
"""


from typing import Any, Required, TypedDict


VERSION_MAPPING_DEFAULT: dict[str, Any] = {}
""" Default value of the field path 'Versions configuration version-mapping' """



# | Versions configuration.
VersionsConfiguration = TypedDict('VersionsConfiguration', {
    # | The additional packages to be added to the versions
    'additional-packages': dict[str, Any],
    # | examples:
    # |   - datasource: pypi
    # |     package: python
    # |   - datasource: docker
    # |     package: ubuntu
    # |   - datasource: docker
    # |     package: debian
    # |   - datasource: node-version
    # |     package: node
    # |   - datasource: package
    # |     package: java
    # |   - datasource: package
    # |     package: redis
    # |   - datasource: package
    # |     package: haproxy
    # |   - datasource: package
    # |     package: kubernetes
    # |   - datasource: package
    # |     package: tomcat
    # |   - datasource: package
    # |     package: postgres
    'external-packages': list["_VersionsConfigurationExternalPackagesItem"],
    # | The package extractor by datasource
    'package-extractor': dict[str, "_VersionsConfigurationPackageExtractorAdditionalproperties"],
    # | Version mapping.
    # | 
    # | Mapping of version to the branch name
    # | 
    # | default:
    # |   {}
    'version-mapping': dict[str, str],
}, total=False)


class _VersionsConfigurationExternalPackagesItem(TypedDict, total=False):
    package: Required[str]
    """
    The name of the package from https://endoflife.date

    Required property
    """

    datasource: Required[str]
    """
    The datasource of the dependencies

    Required property
    """



_VersionsConfigurationPackageExtractorAdditionalproperties = dict[str, list["_VersionsConfigurationPackageExtractorAdditionalpropertiesAdditionalpropertiesItem"]]
""" The package extractor by package name """



_VersionsConfigurationPackageExtractorAdditionalpropertiesAdditionalpropertiesItem = TypedDict('_VersionsConfigurationPackageExtractorAdditionalpropertiesAdditionalpropertiesItem', {
    # | The regular expression used to extract value from the package version
    'version-extractor': str,
    # | The type of datasource
    'datasource': str,
    # | The list of the required values to do the correspondence
    'requires': list[str],
    # | The name of the package that can be build from the extracted values
    # | 
    # | Required property
    'package': Required[str],
    # | The version of the package that can be build from the extracted values
    # | 
    # | Required property
    'version': Required[str],
}, total=False)
