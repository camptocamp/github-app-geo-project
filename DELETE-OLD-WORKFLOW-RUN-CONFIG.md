# Delete old workflow runs configuration

## Properties

- <a id="properties/rules"></a>**`rules`** _(array)_
  - <a id="properties/rules/items"></a>**Items** _(object)_: A rule to filter the list of workflow runs.
    - <a id="properties/rules/items/properties/older-than-days"></a>**`older-than-days`** _(integer, required)_
    - <a id="properties/rules/items/properties/workflow"></a>**`workflow`** _(string)_
    - <a id="properties/rules/items/properties/actor"></a>**`actor`** _(string)_
    - <a id="properties/rules/items/properties/branch"></a>**`branch`** _(string)_
    - <a id="properties/rules/items/properties/event"></a>**`event`** _(string)_
    - <a id="properties/rules/items/properties/status"></a>**`status`** _(string)_
