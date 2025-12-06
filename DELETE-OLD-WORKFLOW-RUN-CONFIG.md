# Delete old workflow runs configuration

## Properties

- <a id="properties/rules"></a>**`rules`** *(array)*
  - <a id="properties/rules/items"></a>**Items** *(object)*: A rule to filter the list of workflow runs.
    - <a id="properties/rules/items/properties/older-than-days"></a>**`older-than-days`** *(integer, required)*
    - <a id="properties/rules/items/properties/workflow"></a>**`workflow`** *(string)*
    - <a id="properties/rules/items/properties/actor"></a>**`actor`** *(string)*
    - <a id="properties/rules/items/properties/branch"></a>**`branch`** *(string)*
    - <a id="properties/rules/items/properties/event"></a>**`event`** *(string)*
    - <a id="properties/rules/items/properties/status"></a>**`status`** *(string)*: Must be one of: "completed", "action_required", "cancelled", "failure", "neutral", "skipped", "stale", "success", "timed_out", "in_progress", "queued", "requested", "waiting", or "pending".
