Create a transversal dashboard with all the failing workflows.

### Functionality Details

This module aggregates information about failing workflows across multiple branches and repositories into a single dashboard. It helps maintainers quickly identify and address issues affecting the project's CI/CD pipelines.

- **Dashboard Creation**: Collects data from failed workflows and organizes it into a centralized dashboard.
- **Branch Stabilization**: Uses the [`SECURITY.md`](https://github.com/camptocamp/c2cciutils/wiki/SECURITY.md) file from the default branch to identify stabilization branches.
- **Workflow Analysis**: Provides insights into recurring issues and trends in workflow failures.

### Usage Notes

- Ensure the `SECURITY.md` file is up-to-date and accurately reflects the stabilization branches.
- Use the dashboard to prioritize fixes for workflows affecting critical branches.
- Regularly review the dashboard
