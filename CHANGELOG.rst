=========
Changelog
=========

All notable changes to the FlexMeasures Client are documented in this file.

The format is based on `Keep a Changelog <https://keepachangelog.com/en/1.1.0/>`_,
and this project adheres to `Semantic Versioning <https://semver.org/spec/v2.0.0.html>`_.
Each entry links to the pull request that made the change.
Changes to the S2 integration are listed in the `S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst>`_,
which each release that changed it links to.
Add your entry under **Unreleased** when you open a pull request.

Unreleased
==========

`All changes since v0.9.6 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.9.6...main>`__.

Added
-----

* Optional ``rate_limit_notifier`` and ``job_status_notifier`` callbacks, to report rate limits and the progress of jobs being waited for, instead of logging them [`#225 <https://github.com/FlexMeasures/flexmeasures-client/pull/225>`__]
* ``get_automations``, ``get_automation``, ``add_automation``, ``update_automation``, ``delete_automation``, ``trigger_automation`` and ``trigger_and_await_automation``, to manage and run automations through the API on FlexMeasures v1.1.0 and up [`#230 <https://github.com/FlexMeasures/flexmeasures-client/pull/230>`__]

Fixed
-----

* Respect HTTP 429 (Too Many Requests) responses, waiting as long as the server's ``Retry-After`` header says, or backing off exponentially [`#225 <https://github.com/FlexMeasures/flexmeasures-client/pull/225>`__]
* Upgrade dependencies for security [`#234 <https://github.com/FlexMeasures/flexmeasures-client/pull/234>`__, `#235 <https://github.com/FlexMeasures/flexmeasures-client/pull/235>`__]

v0.9.6 | 2026-09-16
===================

`All changes since v0.9.5 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.9.5...v0.9.6>`__.

Added
-----

* ``trigger_report`` and ``trigger_and_await_report``, to compute reports through the API on FlexMeasures v1.1.0 and up [`#224 <https://github.com/FlexMeasures/flexmeasures-client/pull/224>`__]
* ``get_job_status`` and ``wait_for_job``, to look up and wait for any background job, with exponential backoff [`#224 <https://github.com/FlexMeasures/flexmeasures-client/pull/224>`__]
* Send the client version in a ``FlexMeasures-Client-Version`` request header, so that a server can recognise clients that misread a changed response [`#226 <https://github.com/FlexMeasures/flexmeasures-client/pull/226>`__]
* The HEMS tutorial is ready for production use, with HTTPS support, asynchronous data ingestion, data-only cleanup and recovery of interrupted runs [`#222 <https://github.com/FlexMeasures/flexmeasures-client/pull/222>`__]

Changed
-------

* Separate settings for HTTP requests (``request_timeout``, ``request_retry_interval``, ``request_retry_timeout``, ``max_request_attempts``) and for waiting on jobs (``job_polling_interval``, ``job_polling_max_interval``, ``job_polling_timeout``) [`#224 <https://github.com/FlexMeasures/flexmeasures-client/pull/224>`__]

Deprecated
----------

* The ``max_polling_steps``, ``polling_timeout`` and ``polling_interval`` arguments, in favour of ``max_request_attempts``, ``request_retry_timeout`` and ``request_retry_interval`` [`#224 <https://github.com/FlexMeasures/flexmeasures-client/pull/224>`__]

v0.9.5 | 2026-08-09
===================

`All changes since v0.9.4 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.9.4...v0.9.5>`__.

Same code as v0.9.4.

v0.9.4 | 2026-08-09
===================

`All changes since v0.9.0 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.9.0...v0.9.4>`__.

Released from ``main``, so it also contains the fixes of v0.9.1 to v0.9.3, which were released from a maintenance branch.

Added
-----

* A ``unit`` for sensor data uploaded as a file [`#221 <https://github.com/FlexMeasures/flexmeasures-client/pull/221>`__]

Changed
-------

* Trigger all schedules through the asset scheduling endpoint, falling back to the sensor endpoint only for FlexMeasures servers before v0.27.0 [`#196 <https://github.com/FlexMeasures/flexmeasures-client/pull/196>`__, `#208 <https://github.com/FlexMeasures/flexmeasures-client/pull/208>`__]
* Poll on any HTTP 202 (Accepted) response, as FlexMeasures API v3.0-32 answers job triggers and unfinished jobs that way [`#214 <https://github.com/FlexMeasures/flexmeasures-client/pull/214>`__, `#219 <https://github.com/FlexMeasures/flexmeasures-client/pull/219>`__]

Fixed
-----

* Name the field that forces a new scheduling job the way the server expects, also for development versions of FlexMeasures [`#218 <https://github.com/FlexMeasures/flexmeasures-client/pull/218>`__]
* The HEMS tutorial can be run in Docker, and no longer misses failed asynchronous uploads [`#216 <https://github.com/FlexMeasures/flexmeasures-client/pull/216>`__]

v0.9.3 | 2026-07-13
===================

`All changes since v0.9.2 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.9.2...v0.9.3>`__.

Fixed
-----

* Install on Python 3.13 and 3.14 again, and loosen dependency floors that conflicted with Home Assistant's pins [`#220 <https://github.com/FlexMeasures/flexmeasures-client/pull/220>`__]

v0.9.2 | 2026-06-16
===================

`All changes since v0.9.1 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.9.1...v0.9.2>`__.

Fixed
-----

* ``parse_json_fields`` works when updating a sensor or an asset, too [`#215 <https://github.com/FlexMeasures/flexmeasures-client/pull/215>`__]

v0.9.1 | 2026-05-27
===================

`All changes since v0.9.0 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.9.0...v0.9.1>`__.

Changed
-------

* Accept any 2xx status code as success, ahead of FlexMeasures answering some requests with HTTP 202 [`#206 <https://github.com/FlexMeasures/flexmeasures-client/pull/206>`__]

Fixed
-----

* Updates were JSON-encoded again on every retry [`#209 <https://github.com/FlexMeasures/flexmeasures-client/pull/209>`__]

Security
--------

* Require urllib3 v2.7.0 [`#204 <https://github.com/FlexMeasures/flexmeasures-client/pull/204>`__]

v0.9.0 | 2026-04-21
===================

`All changes since v0.8.1 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.8.1...v0.9.0>`__.

S2 changes: see `v0.9.0 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v090--2026-04-21>`__.

Added
-----

* ``trigger_forecast``, ``get_forecast`` and ``trigger_and_get_forecast``, for the forecasting endpoints of FlexMeasures v0.31.0 and up [`#180 <https://github.com/FlexMeasures/flexmeasures-client/pull/180>`__]
* A ``unit`` for ``get_schedule`` and ``trigger_and_get_schedule``, and a ``convert_units`` helper for W, kW and MW [`#194 <https://github.com/FlexMeasures/flexmeasures-client/pull/194>`__]
* Keep track of the server version through the ``FlexMeasures-Version`` response header [`#193 <https://github.com/FlexMeasures/flexmeasures-client/pull/193>`__]
* Warn when an ``aggregate-power`` flex-context field is sent to a server older than v0.31.0, which would ignore it [`#168 <https://github.com/FlexMeasures/flexmeasures-client/pull/168>`__]
* The HEMS tutorial stores an aggregate power schedule for each site `#168 <https://github.com/FlexMeasures/flexmeasures-client/pull/168>`__, and triggers its forecasts through the API [`#190 <https://github.com/FlexMeasures/flexmeasures-client/pull/190>`__]

Changed
-------

* Back off exponentially between polling retries [`#184 <https://github.com/FlexMeasures/flexmeasures-client/pull/184>`__]
* Develop with uv instead of tox and PyScaffold, building with hatchling [`#177 <https://github.com/FlexMeasures/flexmeasures-client/pull/177>`__]

Removed
-------

* Support for Python 3.9 [`#177 <https://github.com/FlexMeasures/flexmeasures-client/pull/177>`__]

Fixed
-----

* Also parse the ``sensors_to_show`` and ``kpi_sensors_to_show`` JSON fields [`#197 <https://github.com/FlexMeasures/flexmeasures-client/pull/197>`__]

v0.8.1 | 2026-02-07
===================

`All changes since v0.8.0 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.8.0...v0.8.1>`__.

S2 changes: see `v0.8.1 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v081--2026-02-07>`__.

Added
-----

* ``get_asset``, to fetch one asset with all of its fields [`#159 <https://github.com/FlexMeasures/flexmeasures-client/pull/159>`__]
* Select one of the custom schedulers registered on the server [`#161 <https://github.com/FlexMeasures/flexmeasures-client/pull/161>`__]
* A ``parse_json_fields`` argument for the asset and sensor methods, to receive ``attributes``, ``flex_context`` and ``flex_model`` as dictionaries rather than JSON strings [`#164 <https://github.com/FlexMeasures/flexmeasures-client/pull/164>`__]
* Log the schedule ID when a schedule is triggered [`#174 <https://github.com/FlexMeasures/flexmeasures-client/pull/174>`__]
* A CEMS tutorial, scheduling several sites of an energy community in parallel [`#148 <https://github.com/FlexMeasures/flexmeasures-client/pull/148>`__]

Deprecated
----------

* Calling the asset and sensor methods without ``parse_json_fields``, as JSON fields will be parsed by default in a future version [`#164 <https://github.com/FlexMeasures/flexmeasures-client/pull/164>`__]

Fixed
-----

* Several fixes to the HEMS and CEMS tutorials, such as running several communities side by side [`#163 <https://github.com/FlexMeasures/flexmeasures-client/pull/163>`__, `#166 <https://github.com/FlexMeasures/flexmeasures-client/pull/166>`__, `#167 <https://github.com/FlexMeasures/flexmeasures-client/pull/167>`__, `#169 <https://github.com/FlexMeasures/flexmeasures-client/pull/169>`__, `#170 <https://github.com/FlexMeasures/flexmeasures-client/pull/170>`__, `#171 <https://github.com/FlexMeasures/flexmeasures-client/pull/171>`__, `#172 <https://github.com/FlexMeasures/flexmeasures-client/pull/172>`__]

v0.8.0 | 2026-01-14
===================

`All changes since v0.7.0 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.7.0...v0.8.0>`__.

S2 changes: see `v0.8.0 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v080--2026-01-14>`__.

Added
-----

* ``post_sensor_data``, to upload sensor data as JSON or from a file `#135 <https://github.com/FlexMeasures/flexmeasures-client/pull/135>`__, optionally as measured instantly [`#134 <https://github.com/FlexMeasures/flexmeasures-client/pull/134>`__]
* ``get_asset_types`` [`#136 <https://github.com/FlexMeasures/flexmeasures-client/pull/136>`__]
* ``get_assets`` accepts all the filters of the API, except pagination `#129 <https://github.com/FlexMeasures/flexmeasures-client/pull/129>`__, and selects asset subtrees and fields with ``root``, ``depth`` and ``fields`` on FlexMeasures v0.31.0 and up [`#156 <https://github.com/FlexMeasures/flexmeasures-client/pull/156>`__]
* ``update_asset`` can update the ``flex_model`` `#130 <https://github.com/FlexMeasures/flexmeasures-client/pull/130>`__ `#157 <https://github.com/FlexMeasures/flexmeasures-client/pull/157>`__, the location `#157 <https://github.com/FlexMeasures/flexmeasures-client/pull/157>`__, and ``sensors_to_show_as_kpis`` [`#143 <https://github.com/FlexMeasures/flexmeasures-client/pull/143>`__]
* A ``prior`` can be passed as a datetime, like the ``start`` [`#144 <https://github.com/FlexMeasures/flexmeasures-client/pull/144>`__]
* ``get_schedule`` without a ``duration`` lets the server decide how much of the schedule to send [`#155 <https://github.com/FlexMeasures/flexmeasures-client/pull/155>`__]
* Pass an existing logger to the client [`#155 <https://github.com/FlexMeasures/flexmeasures-client/pull/155>`__]
* Explain on a 404 response that the server is too old for the feature used [`#151 <https://github.com/FlexMeasures/flexmeasures-client/pull/151>`__]
* A HEMS tutorial, building a household from scratch and scheduling it `#134 <https://github.com/FlexMeasures/flexmeasures-client/pull/134>`__, and a smaller example script [`#131 <https://github.com/FlexMeasures/flexmeasures-client/pull/131>`__]

Changed
-------

* Use the sensor data endpoints of FlexMeasures v0.28.0 and up, without entity addresses [`#139 <https://github.com/FlexMeasures/flexmeasures-client/pull/139>`__]
* Double the request timeout, from 20 to 40 seconds [`#155 <https://github.com/FlexMeasures/flexmeasures-client/pull/155>`__]

Deprecated
----------

* ``post_measurements``, in favour of ``post_sensor_data`` [`#135 <https://github.com/FlexMeasures/flexmeasures-client/pull/135>`__]

Fixed
-----

* The flex-model and flex-context are optional when triggering a schedule [`#141 <https://github.com/FlexMeasures/flexmeasures-client/pull/141>`__]

v0.7.0 | 2025-07-15
===================

`All changes since v0.6.0 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.6.0...v0.7.0>`__.

Added
-----

* ``delete_asset`` and ``delete_sensor`` [`#124 <https://github.com/FlexMeasures/flexmeasures-client/pull/124>`__]
* Assets can be created and updated with ``sensors_to_show`` and a ``flex_context`` [`#124 <https://github.com/FlexMeasures/flexmeasures-client/pull/124>`__]
* ``get_sensors`` can be filtered by asset [`#126 <https://github.com/FlexMeasures/flexmeasures-client/pull/126>`__]
* A complete example script [`#127 <https://github.com/FlexMeasures/flexmeasures-client/pull/127>`__]

v0.6.0 | 2025-07-09
===================

`All changes since v0.5.6 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.5.6...v0.6.0>`__.

S2 changes: see `v0.6.0 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v060--2025-07-09>`__.

Added
-----

* Multi-asset scheduling [`#117 <https://github.com/FlexMeasures/flexmeasures-client/pull/117>`__]
* Set a parent asset when adding an asset [`#118 <https://github.com/FlexMeasures/flexmeasures-client/pull/118>`__]
* Raise an informative error when multi-asset scheduling is attempted on a server that does not support it [`#119 <https://github.com/FlexMeasures/flexmeasures-client/pull/119>`__]

v0.5.6 | 2025-05-26
===================

`All changes since v0.5.4 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.5.4...v0.5.6>`__.

S2 changes: see `v0.5.6 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v056--2025-05-26>`__.

v0.5.4 | 2025-05-23
===================

`All changes since v0.5.3 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.5.3...v0.5.4>`__.

S2 changes: see `v0.5.4 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v054--2025-05-23>`__.

v0.5.3 | 2025-05-16
===================

`All changes since v0.5.2 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.5.2...v0.5.3>`__.

S2 changes: see `v0.5.3 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v053--2025-05-16>`__.

v0.5.2 | 2025-05-16
===================

`All changes since v0.5.1 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.5.1...v0.5.2>`__.

Tagged, but not published on PyPI.

v0.5.1 | 2025-05-16
===================

`All changes since v0.5.0 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.5.0...v0.5.1>`__.

S2 changes: see `v0.5.1 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v051--2025-05-16>`__.

v0.5.0 | 2025-05-16
===================

`All changes since v0.4.7 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.4.7...v0.5.0>`__.

S2 changes: see `v0.5.0 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v050--2025-05-16>`__.

v0.4.7 | 2025-04-23
===================

`All changes since v0.4.6 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.4.6...v0.4.7>`__.

S2 changes: see `v0.4.7 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v047--2025-04-23>`__.

v0.4.6 | 2025-03-27
===================

`All changes since v0.4.5 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.4.5...v0.4.6>`__.

Fixed
-----

* Internal fixes.

v0.4.5 | 2025-03-25
===================

`All changes since v0.4.4 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.4.4...v0.4.5>`__.

S2 changes: see `v0.4.5 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v045--2025-03-25>`__.

v0.4.4 | 2025-03-18
===================

`All changes since v0.4.3 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.4.3...v0.4.4>`__.

S2 changes: see `v0.4.4 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v044--2025-03-18>`__.

v0.4.3 | 2025-02-18
===================

`All changes since v0.4.2 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.4.2...v0.4.3>`__.

S2 changes: see `v0.4.3 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v043--2025-02-18>`__.

v0.4.2 | 2025-02-17
===================

`All changes since v0.4.1 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.4.1...v0.4.2>`__.

S2 changes: see `v0.4.2 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v042--2025-02-17>`__.

v0.4.1 | 2025-02-06
===================

`All changes since v0.4.0 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.4.0...v0.4.1>`__.

S2 changes: see `v0.4.1 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v041--2025-02-06>`__.

v0.4.0 | 2025-02-06
===================

`All changes since v0.3.3 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.3.3...v0.4.0>`__.

S2 changes: see `v0.4.0 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v040--2025-02-06>`__.

v0.3.3 | 2025-01-30
===================

`All changes since v0.3.2 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.3.2...v0.3.3>`__.

S2 changes: see `v0.3.3 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v033--2025-01-30>`__.

Changed
-------

* **Breaking:** S2 support is an optional extra, so install ``flexmeasures-client[s2]`` to use it [`#102 <https://github.com/FlexMeasures/flexmeasures-client/pull/102>`__]

v0.3.2 | 2025-01-24
===================

`All changes since v0.3.1 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.3.1...v0.3.2>`__.

Changed
-------

* Remove the pin on aiohttp [`#101 <https://github.com/FlexMeasures/flexmeasures-client/pull/101>`__]

v0.3.1 | 2025-01-24
===================

`All changes since v0.3.0 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.3.0...v0.3.1>`__.

S2 changes: see `v0.3.1 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v031--2025-01-24>`__.

v0.3.0 | 2025-01-23
===================

`All changes since v0.2.6 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.2.6...v0.3.0>`__.

S2 changes: see `v0.3.0 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v030--2025-01-23>`__.

v0.2.6 | 2024-12-13
===================

`All changes since v0.2.5 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.2.5...v0.2.6>`__.

Added
-----

* ``get_account`` [`#94 <https://github.com/FlexMeasures/flexmeasures-client/pull/94>`__]

v0.2.5 | 2024-11-12
===================

`All changes since v0.2.4 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.2.4...v0.2.5>`__.

S2 changes: see `v0.2.5 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v025--2024-11-12>`__.

v0.2.4 | 2024-10-02
===================

`All changes since v0.2.3 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.2.3...v0.2.4>`__.

Fixed
-----

* Do not require a session when creating the client.

v0.2.3 | 2024-10-02
===================

`All changes since v0.2.2 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.2.2...v0.2.3>`__.

Tagged, but not published on PyPI.

Changed
-------

* Rename ``start_session()`` to ``ensure_session()``.

v0.2.2 | 2024-10-02
===================

`All changes since v0.2.1 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.2.1...v0.2.2>`__.

Tagged, but not published on PyPI.

Fixed
-----

* Do not create an HTTP session on import already [`#87 <https://github.com/FlexMeasures/flexmeasures-client/pull/87>`__]
* Take a port number given as part of the ``host`` into account [`#88 <https://github.com/FlexMeasures/flexmeasures-client/pull/88>`__]

v0.2.1 | 2024-03-12
===================

`All changes since v0.2.0 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.2.0...v0.2.1>`__.

Changed
-------

* **Breaking:** exception classes moved to ``flexmeasures_client.exceptions`` [`#80 <https://github.com/FlexMeasures/flexmeasures-client/pull/80>`__]

v0.2.0 | 2024-03-04
===================

`All changes since v0.1.11 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.1.11...v0.2.0>`__.

S2 changes: see `v0.2.0 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v020--2024-03-04>`__.

Added
-----

* Type hints, checked with mypy [`#74 <https://github.com/FlexMeasures/flexmeasures-client/pull/74>`__]

Changed
-------

* Handle all response status codes in one place [`#72 <https://github.com/FlexMeasures/flexmeasures-client/pull/72>`__]

Fixed
-----

* Use ``URL`` objects instead of strings for URLs [`#79 <https://github.com/FlexMeasures/flexmeasures-client/pull/79>`__]

v0.1.11 | 2023-11-02
====================

`All changes since v0.1.10 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.1.10...v0.1.11>`__.

Added
-----

* Follow a redirect to the fallback schedule [`#71 <https://github.com/FlexMeasures/flexmeasures-client/pull/71>`__]

v0.1.10 | 2023-09-17
====================

`All changes since v0.1.9 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.1.9...v0.1.10>`__.

Added
-----

* A static method to convert the units of a schedule [`#68 <https://github.com/FlexMeasures/flexmeasures-client/pull/68>`__]

v0.1.9 | 2023-09-14
===================

`All changes since v0.1.8 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.1.8...v0.1.9>`__.

Added
-----

* Schedule with a flex-model and a flex-context [`#65 <https://github.com/FlexMeasures/flexmeasures-client/pull/65>`__]
* Update the attributes of sensors and assets with a dictionary [`#64 <https://github.com/FlexMeasures/flexmeasures-client/pull/64>`__]

v0.1.8 | 2023-09-01
===================

`All changes since v0.1.7 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.1.7...v0.1.8>`__.

S2 changes: see `v0.1.8 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v018--2023-09-01>`__.

v0.1.7 | 2023-08-31
===================

`All changes since v0.1.6 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.1.6...v0.1.7>`__.

S2 changes: see `v0.1.7 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v017--2023-08-31>`__.

v0.1.6 | 2023-08-31
===================

`All changes since v0.1.5 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.1.5...v0.1.6>`__.

S2 changes: see `v0.1.6 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v016--2023-08-31>`__.

Changed
-------

* Only a sensor ID is needed, not an entity address [`#58 <https://github.com/FlexMeasures/flexmeasures-client/pull/58>`__]

v0.1.5 | 2023-08-16
===================

`All changes since v0.1.4 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.1.4...v0.1.5>`__.

S2 changes: see `v0.1.5 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v015--2023-08-16>`__.

Added
-----

* ``get_sensor``, ``add_sensor``, ``add_asset`` and ``update_asset`` [`#55 <https://github.com/FlexMeasures/flexmeasures-client/pull/55>`__]
* Info and debug logging [`#47 <https://github.com/FlexMeasures/flexmeasures-client/pull/47>`__]

v0.1.4 | 2023-06-27
===================

`All changes since v0.1.3 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.1.3...v0.1.4>`__.

Added
-----

* ``get_sensor_data`` [`#43 <https://github.com/FlexMeasures/flexmeasures-client/pull/43>`__]
* A minimum and maximum state of charge when scheduling storage [`#45 <https://github.com/FlexMeasures/flexmeasures-client/pull/45>`__]

Changed
-------

* Return basic Python types, such as dictionaries and lists [`#48 <https://github.com/FlexMeasures/flexmeasures-client/pull/48>`__]

Fixed
-----

* The duration in ``get_schedule`` [`#44 <https://github.com/FlexMeasures/flexmeasures-client/pull/44>`__]
* Re-authenticate once when a request is unauthorized [`#50 <https://github.com/FlexMeasures/flexmeasures-client/pull/50>`__]
* Ship the S2 message definitions in a submodule, so that the package installs from PyPI [`#52 <https://github.com/FlexMeasures/flexmeasures-client/pull/52>`__]

v0.1.3 | 2023-06-02
===================

`All changes since v0.1.2 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.1.2...v0.1.3>`__.

Tagged, but not published on PyPI.

Added
-----

* ``trigger_and_get_schedule``, to trigger a schedule and get it in one call [`#42 <https://github.com/FlexMeasures/flexmeasures-client/pull/42>`__]

v0.1.2 | 2023-05-30
===================

`All changes since v0.1.1 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.1.1...v0.1.2>`__.

Tagged, but not published on PyPI.

S2 changes: see `v0.1.2 in the S2 CEM changelog <https://github.com/FlexMeasures/flexmeasures-client/blob/main/CHANGELOG-S2.rst#v012--2023-05-30>`__.

v0.1.1 | 2023-05-26
===================

`All changes since v0.1.0 <https://github.com/FlexMeasures/flexmeasures-client/compare/v0.1.0...v0.1.1>`__.

Fixed
-----

* An import statement [`#41 <https://github.com/FlexMeasures/flexmeasures-client/pull/41>`__]

v0.1.0 | 2023-05-24
===================

Added
-----

* A basic client to authenticate, get assets and sensors, post measurements, and trigger and get schedules, with polling and error handling [`#33 <https://github.com/FlexMeasures/flexmeasures-client/pull/33>`__]
