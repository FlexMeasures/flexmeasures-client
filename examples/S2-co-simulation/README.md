# A FlexMeasures stack for an S2 co-simulation

`docker-compose.cem.yml` runs FlexMeasures with one S2 CEM per simulated home,
for a co-simulation whose resource managers connect over websockets, such as a
community of homes steered at once. How to start it is at the top of the file.

Each CEM is the example server (`src/flexmeasures_client/s2/script/websockets_server.py`)
with two switches on:

- `S2_ACCEPT_SIMULATION_METADATA`: a resource manager wraps each message with the
  simulated time, `{"message": ..., "metadata": {"dt": ...}}`, and the CEM, and every
  handler it registers, go by that time (see `flexmeasures_client.s2.clock`).
  `dt` has to name its time zone. Without the switch, a wrapped message is refused
  as invalid, from the handshake on.
- `S2_SITE_PER_RESOURCE_MANAGER`: each resource manager gets a site of its own,
  named after it, with the sensors in `flexmeasures_client.s2.site.FRBC_SITE_SENSORS`.
  Messages are answered with a temporary error until its site is set up, which a
  resource manager retries. Its schedules start from the simulated now and cover
  24 hours. The site's prices are for whoever runs the simulation to post.

A third switch is off by default:

- `S2_CORRECT_USAGE`: a usage forecast that no plan can absorb within the storage's
  hard bounds is corrected towards zero, instead of leaving the home without a plan.
  Each site gets a `usage correction` sensor, which records where the plan corrected
  the forecast. A negative expected usage is then planned as the gain it states,
  rather than as none. The request also sets the breach prices of the fill-level band
  and the site's capacity relative to the market, rather than leaving them to
  FlexMeasures' defaults. It needs FlexMeasures v1.1.0 or newer.

FlexMeasures reads three settings a co-simulation needs from its instance config
(`flexmeasures-instance/flexmeasures.cfg` in the flexmeasures checkout), not from the environment:

```python
# A steering round replans a window at the same simulated moment, and that plan replaces the one before.
# Without this, the scheduler cannot save the second plan, and the home gets no instructions for its window.
FLEXMEASURES_ALLOW_DATA_OVERWRITE = True
# Every home triggers a schedule each round, all from one account, far above the default of 10 per 5 minutes,
# and every trigger past the limit waits minutes before it is retried.
FLEXMEASURES_API_TRIGGER_RATE_LIMIT = "1000 per minute"
# A bound on each solve, since a tail of degenerate problems otherwise proves optimality past the job timeout.
FLEXMEASURES_LP_SOLVER_OPTIONS = {"time_limit": "15"}
```

A co-simulation reads the sites by sensor name: `price`, `production price`,
`power` (the schedule), `measured-power` (what the home measured) and
`state of charge`.

The stack uses FlexMeasures' own development database service, `dev-db`,
whose data FlexMeasures' compose file keeps in `docker-compose-data/dev-db`
inside the flexmeasures checkout it runs from.
A compose project name of its own does not separate that.
To keep a development database you care about out of it,
run it from a folder of its own holding checkouts named `flexmeasures` and `flexmeasures-client`:
the overlay mounts `../flexmeasures` and `../flexmeasures-client`, so those names are what it runs.
Git worktrees will do, with one thing to add: FlexMeasures' image reads its version from git, which it cannot through a worktree.
Give it as the build argument `FLEXMEASURES_VERSION` to each service built from FlexMeasures' Dockerfile, in one more overlay:

```yaml
x-version: &version
  build:
    context: .
    dockerfile: Dockerfile
    args:
      FLEXMEASURES_VERSION: "1.1.0"

services:
  server:
    <<: *version
  worker:
    <<: *version
  # and likewise worker-2, worker-3, automation-runner and each cem
```

Its host ports, 5000, 5433, 6380, 1025, 8025 and 8080 to 8084, have to be free too;
where they are not, an extra overlay can move them with compose's `!override`.

It runs FlexMeasures from the flexmeasures checkout, which is mounted over the code the image installed,
with this client installed into the image's virtualenv, which is what FlexMeasures and its plugins run from.
The UI's third-party libraries are fetched into the installed package when the image is built, so a UI served from the mounted checkout may lack them; the API does not need them.
The server trusts the name the CEMs reach it by, `server`, alongside the loopback names a development server trusts by default.
