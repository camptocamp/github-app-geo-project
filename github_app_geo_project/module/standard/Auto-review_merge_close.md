The auto modules will do some simple operations on modules:

- Auto review: add a positive review on the pull request (don't work as expected because I didn't find a way to make the application as an official reviewer)
- Auto merge: activate the auto-merge option of a pull request, event if I prefer that the modules themselves activate this option.
- Auto close: close the pull request, used to automatically close unwanted pull request created by an application like pre-commit.

The module has an `condition` option to select the affected pull requests.

[Configuration reference](https://github.com/camptocamp/github-app-geo-project/blob/master/AUTO-CONFIG.md).
