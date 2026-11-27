"""
Automatically generated file from a JSON schema.
"""


from typing import Any, Literal, TypedDict


ADVISORY_SEVERITY_THRESHOLD_DEFAULT = 'high'
r""" Default value of the field path 'Snyk configuration advisory-severity-threshold' """



APPLICATIONS_ROOT_DIRECTORY_DEFAULT = 'apps'
r""" Default value of the field path 'Trivy configuration apps-root' """



APPLICATION_DIRECTORY_STRUCTURE_DEFAULT = 'auto'
r""" Default value of the field path 'Trivy configuration app-structure' """



AUTOMATIC_FIX_DEFAULT = True
r""" Default value of the field path 'Snyk configuration auto-fix' """



AdvisorySeverityThreshold = Literal['low'] | Literal['medium'] | Literal['high'] | Literal['critical']
r"""
Advisory severity threshold.

Minimum severity level to create a GitHub Security Advisory (low, medium, high, critical)

default: high
"""
ADVISORYSEVERITYTHRESHOLD_LOW: Literal['low'] = "low"
r"""The values for the 'Advisory severity threshold' enum"""
ADVISORYSEVERITYTHRESHOLD_MEDIUM: Literal['medium'] = "medium"
r"""The values for the 'Advisory severity threshold' enum"""
ADVISORYSEVERITYTHRESHOLD_HIGH: Literal['high'] = "high"
r"""The values for the 'Advisory severity threshold' enum"""
ADVISORYSEVERITYTHRESHOLD_CRITICAL: Literal['critical'] = "critical"
r"""The values for the 'Advisory severity threshold' enum"""



ApplicationDirectoryStructure = Literal['auto'] | Literal['single'] | Literal['two-level']
r"""
Application directory structure.

`auto` detects the layout, `single` for `<apps-root>/<environment>/<app>/`, `two-level` for `<apps-root>/<environment>/<client>/<app>/`

default: auto
"""
APPLICATIONDIRECTORYSTRUCTURE_AUTO: Literal['auto'] = "auto"
r"""The values for the 'Application directory structure' enum"""
APPLICATIONDIRECTORYSTRUCTURE_SINGLE: Literal['single'] = "single"
r"""The values for the 'Application directory structure' enum"""
APPLICATIONDIRECTORYSTRUCTURE_TWO_LEVEL: Literal['two-level'] = "two-level"
r"""The values for the 'Application directory structure' enum"""



# | Audit configuration.
AuditConfiguration = TypedDict('AuditConfiguration', {
    # | Enable audit.
    # | 
    # | default: True
    'enabled': bool,
    # | Snyk configuration.
    'snyk': "SnykConfiguration",
    # | Dpkg configuration.
    'dpkg': "DpkgConfiguration",
    # | Renovate configuration.
    'renovate': "RenovateConfiguration",
    # | Trivy configuration.
    # | 
    # | Scan the container images used by the ArgoCD applications with Trivy
    'trivy': "TrivyConfiguration",
    # | Version mapping.
    # | 
    # | Mapping of version to the branch name
    # | 
    # | default:
    # |   {}
    'version-mapping': dict[str, str],
    # | Pre-commit configuration.
    'pre-commit': "PreCommitConfiguration",
}, total=False)


class AuditModulesConfiguration(TypedDict, total=False):
    r""" Audit modules configuration. """

    audit: "AuditConfiguration"
    r""" Audit configuration. """



DASHBOARD_SEVERITY_THRESHOLD_DEFAULT = 'medium'
r""" Default value of the field path 'Snyk configuration dashboard-severity-threshold' """



DPKG_SOURCES_DEFAULT = {'ubuntu_22_04': [{'url': 'http://archive.ubuntu.com/ubuntu', 'distribution': 'jammy', 'components': ['main', 'restricted', 'universe', 'multiverse']}, {'url': 'http://security.ubuntu.com/ubuntu', 'distribution': 'jammy-security', 'components': ['main', 'restricted', 'universe', 'multiverse']}, {'url': 'http://security.ubuntu.com/ubuntu', 'distribution': 'jammy-updates', 'components': ['main', 'restricted', 'universe', 'multiverse']}], 'ubuntu_24_04': [{'url': 'http://archive.ubuntu.com/ubuntu', 'distribution': 'noble', 'components': ['main', 'restricted', 'universe', 'multiverse']}, {'url': 'http://security.ubuntu.com/ubuntu', 'distribution': 'noble-security', 'components': ['main', 'restricted', 'universe', 'multiverse']}, {'url': 'http://security.ubuntu.com/ubuntu', 'distribution': 'noble-updates', 'components': ['main', 'restricted', 'universe', 'multiverse']}], 'debian_11': [{'url': 'http://deb.debian.org/debian', 'distribution': 'bullseye', 'components': ['main', 'contrib', 'non-free']}, {'url': 'http://deb.debian.org/debian', 'distribution': 'bullseye-updates', 'components': ['main', 'contrib', 'non-free']}, {'url': 'http://security.debian.org/debian-security', 'distribution': 'bullseye-security', 'components': ['main', 'contrib', 'non-free']}], 'debian_12': [{'url': 'http://deb.debian.org/debian', 'distribution': 'bookworm', 'components': ['main', 'contrib', 'non-free']}, {'url': 'http://deb.debian.org/debian', 'distribution': 'bookworm-updates', 'components': ['main', 'contrib', 'non-free']}, {'url': 'http://security.debian.org/debian-security', 'distribution': 'bookworm-security', 'components': ['main', 'contrib', 'non-free']}]}
r""" Default value of the field path 'Dpkg configuration sources' """



DashboardSeverityThreshold = Literal['low'] | Literal['medium'] | Literal['high'] | Literal['critical']
r"""
Dashboard severity threshold.

Minimum severity level to display on the transversal dashboard (low, medium, high, critical)

default: medium
"""
DASHBOARDSEVERITYTHRESHOLD_LOW: Literal['low'] = "low"
r"""The values for the 'Dashboard severity threshold' enum"""
DASHBOARDSEVERITYTHRESHOLD_MEDIUM: Literal['medium'] = "medium"
r"""The values for the 'Dashboard severity threshold' enum"""
DASHBOARDSEVERITYTHRESHOLD_HIGH: Literal['high'] = "high"
r"""The values for the 'Dashboard severity threshold' enum"""
DASHBOARDSEVERITYTHRESHOLD_CRITICAL: Literal['critical'] = "critical"
r"""The values for the 'Dashboard severity threshold' enum"""



class DpkgConfiguration(TypedDict, total=False):
    r""" Dpkg configuration. """

    enabled: bool
    r"""
    Enable dpkg.

    Enable dpkg audit

    default: True
    """

    sources: dict[str, list["_DpkgSourcesAdditionalpropertiesItem"]]
    r"""
    Dpkg sources.

    default:
      debian_11:
      - components:
        - main
        - contrib
        - non-free
        distribution: bullseye
        url: http://deb.debian.org/debian
      - components:
        - main
        - contrib
        - non-free
        distribution: bullseye-updates
        url: http://deb.debian.org/debian
      - components:
        - main
        - contrib
        - non-free
        distribution: bullseye-security
        url: http://security.debian.org/debian-security
      debian_12:
      - components:
        - main
        - contrib
        - non-free
        distribution: bookworm
        url: http://deb.debian.org/debian
      - components:
        - main
        - contrib
        - non-free
        distribution: bookworm-updates
        url: http://deb.debian.org/debian
      - components:
        - main
        - contrib
        - non-free
        distribution: bookworm-security
        url: http://security.debian.org/debian-security
      ubuntu_22_04:
      - components:
        - main
        - restricted
        - universe
        - multiverse
        distribution: jammy
        url: http://archive.ubuntu.com/ubuntu
      - components:
        - main
        - restricted
        - universe
        - multiverse
        distribution: jammy-security
        url: http://security.ubuntu.com/ubuntu
      - components:
        - main
        - restricted
        - universe
        - multiverse
        distribution: jammy-updates
        url: http://security.ubuntu.com/ubuntu
      ubuntu_24_04:
      - components:
        - main
        - restricted
        - universe
        - multiverse
        distribution: noble
        url: http://archive.ubuntu.com/ubuntu
      - components:
        - main
        - restricted
        - universe
        - multiverse
        distribution: noble-security
        url: http://security.ubuntu.com/ubuntu
      - components:
        - main
        - restricted
        - universe
        - multiverse
        distribution: noble-updates
        url: http://security.ubuntu.com/ubuntu
    """



ENABLE_AUDIT_DEFAULT = True
r""" Default value of the field path 'Audit configuration enabled' """



ENABLE_DPKG_DEFAULT = True
r""" Default value of the field path 'Dpkg configuration enabled' """



ENABLE_PRE_COMMIT_DEFAULT = True
r""" Default value of the field path 'Pre-commit configuration enabled' """



ENABLE_RENOVATE_DEFAULT = True
r""" Default value of the field path 'Renovate configuration enabled' """



ENABLE_SNYK_DEFAULT = True
r""" Default value of the field path 'Snyk configuration enabled' """



ENABLE_TRIVY_DEFAULT = True
r""" Default value of the field path 'Trivy configuration enabled' """



ENABLE_TRIVY_PULL_REQUEST_SCAN_DEFAULT = True
r""" Default value of the field path 'Trivy pull request configuration enabled' """



ENVIRONMENTS_DEFAULT = ['int', 'prod']
r""" Default value of the field path 'Trivy configuration environments' """



EXCLUDED_APPLICATIONS_DEFAULT: list[Any] = []
r""" Default value of the field path 'Trivy configuration excluded-apps' """



EXCLUDED_FILES_DEFAULT: list[Any] = []
r""" Default value of the field path 'Snyk configuration excluded-files' """



FILES_NOT_TO_INSTALL_DEFAULT: list[Any] = []
r""" Default value of the field path 'Snyk configuration files-no-install' """



IGNORE_UNFIXED_DEFAULT = False
r""" Default value of the field path 'Trivy configuration ignore-unfixed' """



JAVA_PATH_BY_GRADLE_VERSION_DEFAULT: dict[str, Any] = {}
r""" Default value of the field path 'Snyk configuration java-path-for-gradle' """



PIPENV_SYNC_ARGUMENTS_DEFAULT: list[Any] = []
r""" Default value of the field path 'Snyk configuration pipenv-sync-arguments' """



PIP_INSTALL_ARGUMENTS_DEFAULT: list[Any] = []
r""" Default value of the field path 'Snyk configuration pip-install-arguments' """



POETRY_INSTALL_ARGUMENTS_DEFAULT: list[Any] = []
r""" Default value of the field path 'Snyk configuration poetry-install-arguments' """



# | Pre-commit configuration.
PreCommitConfiguration = TypedDict('PreCommitConfiguration', {
    # | Enable pre-commit.
    # | 
    # | Enable pre-commit audit
    # | 
    # | default: True
    'enabled': bool,
    # | Skip hooks.
    # | 
    # | List of pre-commit hooks to skip
    # | 
    # | default:
    # |   []
    'skip-hooks': list[str],
}, total=False)


class RenovateConfiguration(TypedDict, total=False):
    r""" Renovate configuration. """

    enabled: bool
    r"""
    Enable Renovate.

    Enable Renovate audit

    default: True
    """



SEVERITY_THRESHOLD_DEFAULT = 'critical'
r""" Default value of the field path 'Trivy configuration severity-threshold' """



SKIP_HOOKS_DEFAULT: list[Any] = []
r""" Default value of the field path 'Pre-commit configuration skip-hooks' """



SNYK_FIX_ARGUMENTS_DEFAULT = ['--all-projects']
r""" Default value of the field path 'Snyk configuration fix-arguments' """



SNYK_MONITOR_ARGUMENTS_DEFAULT = ['--all-projects']
r""" Default value of the field path 'Snyk configuration monitor-arguments' """



SNYK_TEST_ARGUMENTS_DEFAULT = ['--all-projects', '--severity-threshold=medium']
r""" Default value of the field path 'Snyk configuration test-arguments' """



SeverityThreshold = Literal['low'] | Literal['medium'] | Literal['high'] | Literal['critical']
r"""
Severity threshold.

Minimum severity level of the vulnerabilities to report and display (low, medium, high, critical)

default: critical
"""
SEVERITYTHRESHOLD_LOW: Literal['low'] = "low"
r"""The values for the 'Severity threshold' enum"""
SEVERITYTHRESHOLD_MEDIUM: Literal['medium'] = "medium"
r"""The values for the 'Severity threshold' enum"""
SEVERITYTHRESHOLD_HIGH: Literal['high'] = "high"
r"""The values for the 'Severity threshold' enum"""
SEVERITYTHRESHOLD_CRITICAL: Literal['critical'] = "critical"
r"""The values for the 'Severity threshold' enum"""



# | Snyk configuration.
SnykConfiguration = TypedDict('SnykConfiguration', {
    # | Enable Snyk.
    # | 
    # | Enable Snyk audit
    # | 
    # | default: True
    'enabled': bool,
    # | Automatic fix.
    # | 
    # | Apply the fixes and open a pull request with them, when false the vulnerabilities are only reported. Can be set per branch in the .github/ghci.yaml file of the stabilization branch
    # | 
    # | default: True
    'auto-fix': bool,
    # | Files not to install.
    # | 
    # | Dependency files that will not be installed
    # | 
    # | default:
    # |   []
    'files-no-install': list[str],
    # | Excluded files.
    # | 
    # | List of regex patterns for file names to exclude from the transversal dashboard and security advisories
    # | 
    # | default:
    # |   []
    'excluded-files': list[str],
    # | Dashboard severity threshold.
    # | 
    # | Minimum severity level to display on the transversal dashboard (low, medium, high, critical)
    # | 
    # | default: medium
    'dashboard-severity-threshold': "DashboardSeverityThreshold",
    # | Advisory severity threshold.
    # | 
    # | Minimum severity level to create a GitHub Security Advisory (low, medium, high, critical)
    # | 
    # | default: high
    'advisory-severity-threshold': "AdvisorySeverityThreshold",
    # | Pip install arguments.
    # | 
    # | Arguments to pass to pip install
    # | 
    # | default:
    # |   []
    'pip-install-arguments': list[str],
    # | Pipenv sync arguments.
    # | 
    # | Arguments to pass to pipenv sync
    # | 
    # | default:
    # |   []
    'pipenv-sync-arguments': list[str],
    # | Poetry install arguments.
    # | 
    # | Arguments to pass to pip install
    # | 
    # | default:
    # |   []
    'poetry-install-arguments': list[str],
    # | Java path by Gradle version.
    # | 
    # | Path to the directory that contains Java executable to use for the Gradle minor version
    # | 
    # | default:
    # |   {}
    'java-path-for-gradle': dict[str, str],
    # | Snyk monitor arguments.
    # | 
    # | Arguments to pass to Snyk monitor
    # | 
    # | default:
    # |   - --all-projects
    'monitor-arguments': list[str],
    # | Snyk test arguments.
    # | 
    # | Arguments to pass to Snyk test
    # | 
    # | default:
    # |   - --all-projects
    # |   - --severity-threshold=medium
    'test-arguments': list[str],
    # | Snyk fix arguments.
    # | 
    # | Arguments to pass to Snyk fix
    # | 
    # | default:
    # |   - --all-projects
    'fix-arguments': list[str],
    # | Snyk monitor configuration.
    'monitor': "SnykMonitorConfiguration",
}, total=False)


# | Snyk monitor configuration.
SnykMonitorConfiguration = TypedDict('SnykMonitorConfiguration', {
    # | Snyk monitor environment.
    # | 
    # | Set the project environment project attribute. To clear the project environment set empty array.
    # | For more information see Project attributes https://docs.snyk.io/getting-started/introduction-to-snyk-projects/view-project-information/project-attributes
    'project-environment': list["_SnykMonitorEnvironmentItem"],
    # | Snyk monitor lifecycle.
    # | 
    # | Set the project lifecycle project attribute. To clear the project lifecycle set empty array.
    # | For more information see Project attributes https://docs.snyk.io/snyk-admin/snyk-projects/project-tags
    'project-lifecycle': list["_SnykMonitorLifecycleItem"],
    # | Snyk monitor business criticality.
    # | 
    # | Set the project business criticality project attribute. To clear the project business criticality set empty array.
    # | For more information see Project attributes https://docs.snyk.io/snyk-admin/snyk-projects/project-tags
    'project-business-criticality': list["_SnykMonitorBusinessCriticalityItem"],
    # | Snyk monitor tags.
    # | 
    # | Set the project tags to one or more values.
    # | To clear the project tags set empty dictionary.
    'project-tags': dict[str, str],
}, total=False)


TRIVY_CONFIG_TARGETS_DEFAULT = ['Dockerfile']
r""" Default value of the field path 'Trivy pull request configuration config-targets' """



TRIVY_IGNORE_FILE_DEFAULT = '.github/.trivyignore.yaml'
r""" Default value of the field path 'Trivy pull request configuration ignore-file' """



TRIVY_SCANNERS_DEFAULT = ['secret', 'misconfig']
r""" Default value of the field path 'Trivy pull request configuration scanners' """



TRIVY_SEVERITY_DEFAULT = 'HIGH,CRITICAL'
r""" Default value of the field path 'Trivy pull request configuration severity' """



# | Trivy configuration.
# | 
# | Scan the container images used by the ArgoCD applications with Trivy
TrivyConfiguration = TypedDict('TrivyConfiguration', {
    # | Enable Trivy.
    # | 
    # | Enable the container images audit, only applied to repositories that have an `apps/` directory of Helm applications
    # | 
    # | default: True
    'enabled': bool,
    # | Applications root directory.
    # | 
    # | The directory that contains the ArgoCD applications, `<apps-root>/<environment>/[<client>/]<app>/Chart.yaml`
    # | 
    # | default: apps
    'apps-root': str,
    # | Environments.
    # | 
    # | The environment sub-directories of `apps-root` that are scanned, matching the directories deployed by ArgoCD
    # | 
    # | default:
    # |   - int
    # |   - prod
    'environments': list[str],
    # | Application directory structure.
    # | 
    # | `auto` detects the layout, `single` for `<apps-root>/<environment>/<app>/`, `two-level` for `<apps-root>/<environment>/<client>/<app>/`
    # | 
    # | default: auto
    'app-structure': "ApplicationDirectoryStructure",
    # | Severity threshold.
    # | 
    # | Minimum severity level of the vulnerabilities to report and display (low, medium, high, critical)
    # | 
    # | default: critical
    'severity-threshold': "SeverityThreshold",
    # | Ignore unfixed.
    # | 
    # | Do not report the vulnerabilities that have no fix yet
    # | 
    # | default: False
    'ignore-unfixed': bool,
    # | Excluded applications.
    # | 
    # | List of regex patterns for application directory paths to exclude from the images scan
    # | 
    # | default:
    # |   []
    'excluded-apps': list[str],
    # | Trivy pull request configuration.
    # | 
    # | Scan the pull request code with Trivy, reported as an action required check
    'pull-request': "TrivyPullRequestConfiguration",
}, total=False)


# | Trivy pull request configuration.
# | 
# | Scan the pull request code with Trivy, reported as an action required check
TrivyPullRequestConfiguration = TypedDict('TrivyPullRequestConfiguration', {
    # | Enable Trivy pull request scan.
    # | 
    # | Scan the pull requests with Trivy
    # | 
    # | default: True
    'enabled': bool,
    # | Trivy scanners.
    # | 
    # | The Trivy scanners run on the pull request with the `trivy fs` command
    # | 
    # | default:
    # |   - secret
    # |   - misconfig
    'scanners': list[str],
    # | Trivy severity.
    # | 
    # | The minimum severities reported by the Trivy scans, as a comma separated list
    # | 
    # | default: HIGH,CRITICAL
    'severity': str,
    # | Trivy ignore file.
    # | 
    # | The path of the Trivy ignore file in the repository, the `--ignorefile` argument is added only if the file exists
    # | 
    # | default: .github/.trivyignore.yaml
    'ignore-file': str,
    # | Trivy config targets.
    # | 
    # | The files scanned with the `trivy config` command, the command is run only for the files that exist in the repository
    # | 
    # | default:
    # |   - Dockerfile
    'config-targets': list[str],
}, total=False)


VERSION_MAPPING_DEFAULT: dict[str, Any] = {}
r""" Default value of the field path 'Audit configuration version-mapping' """



class _DpkgSourcesAdditionalpropertiesItem(TypedDict, total=False):
    url: str
    r"""
    URL.

    URL of the source
    """

    distribution: str
    r"""
    Distribution.

    Distribution of the source
    """

    components: list[str]
    r"""
    Components.

    Components of the source
    """



_SnykMonitorBusinessCriticalityItem = Literal['critical'] | Literal['high'] | Literal['medium'] | Literal['low']
_SNYKMONITORBUSINESSCRITICALITYITEM_CRITICAL: Literal['critical'] = "critical"
r"""The values for the '_SnykMonitorBusinessCriticalityItem' enum"""
_SNYKMONITORBUSINESSCRITICALITYITEM_HIGH: Literal['high'] = "high"
r"""The values for the '_SnykMonitorBusinessCriticalityItem' enum"""
_SNYKMONITORBUSINESSCRITICALITYITEM_MEDIUM: Literal['medium'] = "medium"
r"""The values for the '_SnykMonitorBusinessCriticalityItem' enum"""
_SNYKMONITORBUSINESSCRITICALITYITEM_LOW: Literal['low'] = "low"
r"""The values for the '_SnykMonitorBusinessCriticalityItem' enum"""



_SnykMonitorEnvironmentItem = Literal['frontend'] | Literal['backend'] | Literal['internal'] | Literal['external'] | Literal['mobile'] | Literal['saas'] | Literal['onprem'] | Literal['hosted'] | Literal['distributed']
_SNYKMONITORENVIRONMENTITEM_FRONTEND: Literal['frontend'] = "frontend"
r"""The values for the '_SnykMonitorEnvironmentItem' enum"""
_SNYKMONITORENVIRONMENTITEM_BACKEND: Literal['backend'] = "backend"
r"""The values for the '_SnykMonitorEnvironmentItem' enum"""
_SNYKMONITORENVIRONMENTITEM_INTERNAL: Literal['internal'] = "internal"
r"""The values for the '_SnykMonitorEnvironmentItem' enum"""
_SNYKMONITORENVIRONMENTITEM_EXTERNAL: Literal['external'] = "external"
r"""The values for the '_SnykMonitorEnvironmentItem' enum"""
_SNYKMONITORENVIRONMENTITEM_MOBILE: Literal['mobile'] = "mobile"
r"""The values for the '_SnykMonitorEnvironmentItem' enum"""
_SNYKMONITORENVIRONMENTITEM_SAAS: Literal['saas'] = "saas"
r"""The values for the '_SnykMonitorEnvironmentItem' enum"""
_SNYKMONITORENVIRONMENTITEM_ONPREM: Literal['onprem'] = "onprem"
r"""The values for the '_SnykMonitorEnvironmentItem' enum"""
_SNYKMONITORENVIRONMENTITEM_HOSTED: Literal['hosted'] = "hosted"
r"""The values for the '_SnykMonitorEnvironmentItem' enum"""
_SNYKMONITORENVIRONMENTITEM_DISTRIBUTED: Literal['distributed'] = "distributed"
r"""The values for the '_SnykMonitorEnvironmentItem' enum"""



_SnykMonitorLifecycleItem = Literal['production'] | Literal['development'] | Literal['sandbox']
_SNYKMONITORLIFECYCLEITEM_PRODUCTION: Literal['production'] = "production"
r"""The values for the '_SnykMonitorLifecycleItem' enum"""
_SNYKMONITORLIFECYCLEITEM_DEVELOPMENT: Literal['development'] = "development"
r"""The values for the '_SnykMonitorLifecycleItem' enum"""
_SNYKMONITORLIFECYCLEITEM_SANDBOX: Literal['sandbox'] = "sandbox"
r"""The values for the '_SnykMonitorLifecycleItem' enum"""

