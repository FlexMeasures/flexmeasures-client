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
Git worktrees will do, with one thing to add: FlexMeasures' image reads its version from git,
which it cannot through a worktree, so give it as a build argument,
`FLEXMEASURES_VERSION`, for each service the overlay builds.
Its host ports, 5000, 5433, 6380, 1025, 8025 and 8080 to 8084, have to be free too;
where they are not, an extra overlay can move them with compose's `!override`.

It runs FlexMeasures as its image installs it, with the plugins and this client installed into the image's virtualenv,
and expects a development server, so the server trusts the name the CEMs reach it by, `server`, alongside the loopback names.
