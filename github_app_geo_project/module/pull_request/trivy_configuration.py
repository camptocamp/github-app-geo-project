"""
Automatically generated file from a JSON schema.
"""


from typing import TypedDict


PULL_REQUEST_TRIVY_CONFIG_TARGETS_DEFAULT = ['Dockerfile']
r""" Default value of the field path 'Pull request trivy configuration config-targets' """



PULL_REQUEST_TRIVY_IGNORE_FILE_DEFAULT = '.github/.trivyignore.yaml'
r""" Default value of the field path 'Pull request trivy configuration ignore-file' """



PULL_REQUEST_TRIVY_SCANNERS_DEFAULT = ['secret', 'misconfig']
r""" Default value of the field path 'Pull request trivy configuration scanners' """



PULL_REQUEST_TRIVY_SEVERITY_DEFAULT = 'HIGH,CRITICAL'
r""" Default value of the field path 'Pull request trivy configuration severity' """



# | Pull request trivy configuration.
PullRequestTrivyConfiguration = TypedDict('PullRequestTrivyConfiguration', {
    # | pull request trivy scanners.
    # | 
    # | The Trivy scanners run on the pull request with the `trivy fs` command
    # | 
    # | default:
    # |   - secret
    # |   - misconfig
    'scanners': list[str],
    # | pull request trivy severity.
    # | 
    # | The minimum severities reported by the Trivy scans, as a comma separated list
    # | 
    # | default: HIGH,CRITICAL
    'severity': str,
    # | pull request trivy ignore file.
    # | 
    # | The path of the Trivy ignore file in the repository, the `--ignorefile` argument is added only if the file exists
    # | 
    # | default: .github/.trivyignore.yaml
    'ignore-file': str,
    # | pull request trivy config targets.
    # | 
    # | The files scanned with the `trivy config` command, the command is run only for the files that exist in the repository
    # | 
    # | default:
    # |   - Dockerfile
    'config-targets': list[str],
}, total=False)
