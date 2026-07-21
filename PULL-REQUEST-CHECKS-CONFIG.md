# Pull request checks configuration

## Properties

- <a id="properties/codespell"></a>**`codespell`** *(object)*: The codespell check configuration.
  - <a id="properties/codespell/properties/internal-dictionaries"></a>**`internal-dictionaries`** *(array)*: List of argument that will be added to the codespell command. Default: `["clear", "rare", "informal", "code", "names", "en-GB_to_en-US"]`.
    - <a id="properties/codespell/properties/internal-dictionaries/items"></a>**Items** *(string)*
  - <a id="properties/codespell/properties/arguments"></a>**`arguments`** *(array)*: List of argument that will be added to the codespell command. Default: `["--quiet-level=2", "--check-filenames", "--ignore-words-list=ro"]`.
    - <a id="properties/codespell/properties/arguments/items"></a>**Items** *(string)*
  - <a id="properties/codespell/properties/ignore-re"></a>**`ignore-re`** *(array)*: List of regular expression that should be ignored. Default: `["(.*/)?poetry\\.lock", "(.*/)?package-lock\\.json"]`.
    - <a id="properties/codespell/properties/ignore-re/items"></a>**Items** *(string)*
- <a id="properties/commits-messages"></a>**`commits-messages`**: Check the pull request commits messages.
  - **One of**
    - <a id="properties/commits-messages/oneOf/0"></a>*object*: The commit message check configuration.
      - <a id="properties/commits-messages/oneOf/0/properties/check-fixup"></a>**`check-fixup`** *(boolean)*: Check that we don't have one fixup commit in the pull request. Default: `true`.
      - <a id="properties/commits-messages/oneOf/0/properties/check-squash"></a>**`check-squash`** *(boolean)*: Check that we don't have one squash commit in the pull request. Default: `true`.
      - <a id="properties/commits-messages/oneOf/0/properties/check-first-capital"></a>**`check-first-capital`** *(boolean)*: Check that the all the commits message starts with a capital letter. Default: `true`.
      - <a id="properties/commits-messages/oneOf/0/properties/min-head-length"></a>**`min-head-length`** *(integer)*: Check that the commits message head is at least this long, use 0 to disable. Default: `5`.
      - <a id="properties/commits-messages/oneOf/0/properties/check-no-merge-commits"></a>**`check-no-merge-commits`** *(boolean)*: Check that we don't have merge commits in the pull request. Default: `true`.
      - <a id="properties/commits-messages/oneOf/0/properties/check-no-own-revert"></a>**`check-no-own-revert`** *(boolean)*: Check that we don't have reverted one of our commits in the pull request. Default: `true`.
    - <a id="properties/commits-messages/oneOf/1"></a>*boolean*
- <a id="properties/commits-spell"></a>**`commits-spell`**
  - **One of**
    - <a id="properties/commits-spell/oneOf/0"></a>*object*: Configuration used to check the spelling of the commits.
      - <a id="properties/commits-spell/oneOf/0/properties/only-head"></a>**`only-head`** *(boolean)*: Default: `true`.
    - <a id="properties/commits-spell/oneOf/1"></a>*boolean*
- <a id="properties/pull-request-spell"></a>**`pull-request-spell`**
  - **One of**
    - <a id="properties/pull-request-spell/oneOf/0"></a>*object*: Configuration used to check the spelling of the title and body of the pull request.
      - <a id="properties/pull-request-spell/oneOf/0/properties/only-head"></a>**`only-head`** *(boolean)*: Default: `true`.
    - <a id="properties/pull-request-spell/oneOf/1"></a>*boolean*
