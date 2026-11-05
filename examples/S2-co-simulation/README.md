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

The stack uses FlexMeasures' own development database service, `dev-db`, whose
data FlexMeasures' compose file keeps in `docker-compose-data/dev-db` inside the
flexmeasures checkout. A compose project name of its own does not separate that,
so run the stack from a checkout of its own (a git worktree will do) to keep a
development database you care about out of it. Its host ports, 5000, 5433, 6380
and 8080 to 8084, have to be free too.
