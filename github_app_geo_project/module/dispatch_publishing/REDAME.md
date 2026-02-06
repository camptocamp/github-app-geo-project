Module used to dispatch publishing events.

### Functionality Details

This module automates the dispatching of publishing events created by [`tag-publish`](https://github.com/camptocamp/tag-publish) to Argo CD repositories. It ensures that updates are propagated efficiently across the repositories managed by Argo CD.

- **Event Dispatching**: Listens for publishing events and forwards them to the appropriate Argo CD repositories.
- **Integration with `tag-publish`**: Works seamlessly with the `tag-publish` tool to handle version tagging and publishing workflows.

### Configuration Options

The configuration for this module should be provided in the `DISPATCH_PUBLISH_CONFIG` environment variable as a JSON which lists the destination repositories with:

- `destination_repository`: The repository to dispatch to.
- `event_type`: The event type to dispatch.
- `legacy`: Whether to transform the content to the legacy format (default: `False`).
- `version_type`: The version type to dispatch (optional).
- `package_type`: The package type to dispatch (optional).
- `image_re`: The image regular expression to dispatch (default: `.*`).

### Example Configuration

```json
{
  "destinations": [
    {
      "destination_repository": "repo1",
      "event_type": "tag",
      "legacy": false,
      "version_type": "semver",
      "package_type": "docker",
      "image_re": ".*"
    },
    {
      "destination_repository": "repo2",
      "event_type": "release",
      "legacy": true
    }
  ]
}
```

### Usage Notes

- Ensure the `DISPATCH_PUBLISH_CONFIG` environment variable is correctly set and points to a valid configuration file.
