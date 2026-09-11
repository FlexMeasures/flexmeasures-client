=========
Changelog
=========

Unreleased
==========

- ``post_sensor_data()`` now awaits asynchronous server-side ingestion by
  default (``await_ingestion=True``), restoring read-your-writes semantics
  after FlexMeasures PR #2101 made ``POST .../sensors/data`` (and the file
  upload endpoint) return ``202 Accepted`` with a background job id instead
  of processing synchronously. The client polls the job-status endpoint
  (exponential backoff from 0.25s up to a 2s cap, ``ingestion_polling_timeout``
  seconds total, default 60s) until the job finishes. A failed job raises the
  new ``IngestionFailedError``; a job still pending when polling times out
  logs an ERROR and returns normally, so callers with their own downstream
  safety nets are not blocked indefinitely. Pass ``await_ingestion=False`` to
  opt out and return as soon as the POST is acknowledged, matching the old
  (PR #2101) behavior.

Version 0.1.1
=============

Fix import statement

Version 0.1.0
=============

Basic client that includes the following functionality:
- Request function
- Authentication
- Polling requests
- Get assets
- Get sensors
- Post measurements
- Trigger schedule
- Get schedule
- Error handling

Administration
- Readme
- Changelog
- Authors
- Apache license
