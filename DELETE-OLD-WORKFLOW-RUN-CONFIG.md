# Delete old workflow runs configuration

## Properties

- **`rules`** _(array)_
  - **Items** _(object)_: A rule to filter the list of workflow runs.
    - **`older-than-days`** _(integer, required)_
    - **`workflow`** _(string)_
    - **`actor`** _(string)_
    - **`branch`** _(string)_
    - **`event`** _(string)_
    - **`status`** _(string)_
