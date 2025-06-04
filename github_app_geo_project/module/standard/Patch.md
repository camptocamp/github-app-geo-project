When a workflow failed, see if there is an attached artifact with a name that ends with `.patch`, and try to apply it on the branch, in a new commit.

Usage in the workflow:

```yaml
on:
  pull-request:

jobs:
  <name>:
    steps:
      ...
      - run: git diff --exit-code --patch > diff.patch
      - uses: actions/upload-artifact@v4
        with:
          name: <commit_message>.patch
          path: diff.patch
          retention-days: 1
        if: failure()
```
