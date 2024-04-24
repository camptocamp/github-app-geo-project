# Pull request checks configuration

## Properties

- **`codespell`** _(object)_: The codespell check configuration.
  - **`internal-dictionaries`** _(array)_: List of argument that will be added to the codespell command. Default: `["clear", "rare", "informal", "code", "names", "en-GB_to_en-US"]`.
    - **Items** _(string)_
  - **`arguments`** _(array)_: List of argument that will be added to the codespell command. Default: `["--quiet-level=2", "--check-filenames", "--ignore-words-list=ro"]`.
    - **Items** _(string)_
  - **`ignore-re`** _(array)_: List of regular expression that should be ignored. Default: `["(.*/)?poetry\\.lock", "(.*/)?package-lock\\.json"]`.
    - **Items** _(string)_
- **`commits-messages`**: Check the pull request commits messages.
  - **One of**
    - _object_: The commit message check configuration.
      - **`check-fixup`** _(boolean)_: Check that we don't have one fixup commit in the pull request. Default: `true`.
      - **`check-squash`** _(boolean)_: Check that we don't have one squash commit in the pull request. Default: `true`.
      - **`check-first-capital`** _(boolean)_: Check that the all the commits message starts with a capital letter. Default: `true`.
      - **`min-head-length`** _(integer)_: Check that the commits message head is at least this long, use 0 to disable. Default: `5`.
      - **`check-no-merge-commits`** _(boolean)_: Check that we don't have merge commits in the pull request. Default: `true`.
      - **`check-no-own-revert`** _(boolean)_: Check that we don't have reverted one of our commits in the pull request. Default: `true`.
    - _boolean_
- **`commits-spell`**
  - **One of**
    - _object_: Configuration used to check the spelling of the commits.
      - **`only-head`** _(boolean)_: Default: `true`.
    - _boolean_
- **`pull-request-spell`**
  - **One of**
    - _object_: Configuration used to check the spelling of the title and body of the pull request.
      - **`only-head`** _(boolean)_: Default: `true`.
    - _boolean_
