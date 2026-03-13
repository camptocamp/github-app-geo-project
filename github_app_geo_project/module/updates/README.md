# Updates Module

This module automatically applies updates to repositories.

It is triggered by a daily cron job (`updates-cron`).

## Configuration

The configuration schema is defined in [schema.json](./schema.json).

## Features

- Updates `mheap/json-schema-spell-checker` in `.pre-commit-config.yaml` to the version specified in `versions.yaml` (currently `v2.3.0`).
