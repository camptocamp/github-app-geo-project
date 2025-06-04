Generate the changelog and store it on the release.

First generation is triggered on tag creation or on release creation (especially for HELM releaser), depends on the `create-release` option.

He will also create a milestone and set all the pull requests on it.

If we modify a closed pull request linked to a milestone, the changelog will be updated.

The changelog will contain sections, see the `sections` and `routing` to configure them.

[Configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/CHANGELOG-CONFIG.md).

### Tips

To generate a changelog of an already existing release, you can:

- Create a milestone with the same name as the tag (if not already existing).
- Assign the milestone to a pull request of the release.
- Edit this pull request.
