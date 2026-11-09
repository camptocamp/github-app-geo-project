Generate the changelog and store it on the release.

### Functionality Details

This module automates the generation and management of changelogs for releases. It:

- **Changelog Generation**: Creates a changelog when a tag or release is created, depending on the `create-release` option.
- **Release Creation**: With `create-release` (default: `true`), the creation of a tag creates the matching release, or updates it if it already exists. No release is created for a tag that doesn't exist (anymore) on the repository, because creating a release also creates the missing `tag` on the default branch. With `create-release: false`, the module never creates a release, it only fills the changelog of the releases that already exist.
- **Tag Deletion**: Deleting a tag never recreates it, the module only deletes the releases that still point to the deleted tag.
- **Milestone Management**: Automatically creates a milestone and assigns the merged pull requests of the release to it.
- **Changelog Updates**: Updates the changelog if a closed pull request linked to a milestone is modified.
- **Section Configuration**: Allows customization of changelog sections using `sections` and `routing` options.

### Release Content

The changelog of a release is built from the commits of the comparison between the tag of the
release and the tag of the previous release. For each commit, the merged pull request that
introduced it is used, and the commits that come from no pull request are listed by themselves.

Only the **merged** pull requests are used. GitHub also returns the open pull requests of a branch
for every commit that is not present in the default branch, which is the case of all the commits of
a stabilization branch: without this filter, every open pull request of the branch ended up in the
release, and got the milestone of the release. A pull request closed without being merged is not
part of a release either.

#### Comparison base

The tag of the previous release is looked up by version, and not by Git ancestry:

- the previous tag of `<major>.<minor>.<patch>` is `<major>.<minor>.<patch - 1>`;
- the previous tag of a `.0` is the `.0` of the previous version line: `<major>.<minor - 1>.0`, or,
  on a major change, the `.<highest minor>.0` of the previous major.

The `.0` of a version line is deliberately used in place of the latest patch of that line: it is the
tag created when the `<major>.<minor>` stabilization branch was created, while the versions with a
non-zero patch are backports, committed on that stabilization branch only and therefore not part of
the history of the next version line. That is why the base of `2.9.0` is `2.8.0` and not `2.8.1`,
and the base of `3.0.0` is `2.9.0` and not `2.9.7`.

The pre-releases (`rc`, `dev`) are not releases, they are never used as a comparison base. A
repository that has only pre-release tags therefore gets an empty changelog.

#### Tag names

The whole tag name is parsed as a version, so a pre-release (`2.4.0rc1`, `2.4.0.dev18`) and a tag
with more than three segments (`1.4.5.1`) are different tags from their `2.4.0` / `1.4.5` release.
A tag that is not a version, and that doesn't use the legacy `release<major>` form, is skipped with
a warning.

### Configuration Options

You can configure the changelog module using the `.github/ghci.yaml` file or a similar configuration file.

[Configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/CHANGELOG-CONFIG.md).

### Tips

To generate a changelog for an already existing release:

- Create a milestone with the same name as the tag (if not already existing).
- Assign the milestone to a pull request of the release.
- Edit this pull request.

To remove a tag, and its release, created by mistake:

- Delete the tag, the module deletes the releases that point to it and never recreates them.
- The milestone named after the tag, and the milestone set on the pull requests of the release, are not removed.

### Usage Notes

- Ensure the `sections` and `routing` options are configured to match your project's labeling conventions.
- Regularly review the changelog to ensure it accurately reflects the changes in each release.
