


fm = FlexmeasuresClient(email="guus@seita.nl", password="test")
await fm.get_access_token()
print(fm.access_token)

await fm.post_measurements(
    sensor_id=2,
    start="2023-03-03T10:00+02:00",  # bare in mind DST transitions in case of POSTing local times (for NL, +02:00 becomes +01:00 and vice versa), or stick to POSTing times in UTC (+00:00)
    duration="PT6H",
    values=[15.3, 0, -3.9, 100, 0, -100],
    unit="kW",
)
# print(post_response)

post_response, response_status = await fm.post_schedule_trigger(
    sensor_id=2,
    start="2023-03-03T10:00+02:00",
    soc_unit="kWh",
    soc_at_start=50,
    soc_targets=[
        {
            "value": 100,
            "datetime": "2023-03-03T11:00+02:00",
        }
    ],
)
print(post_response)

time.sleep(3)

schedule_response, schedule_status = await fm.get_schedule(
    sensor_id=2,
    schedule_id=post_response["schedule"],
    duration="PT24H",
)


print(schedule_response)

def get_schedule(
    base_url: str,
    api_version: str,
    auth_token: str,
    sensor_id: int,
    schedule_id: str,
    duration: str,
):
    """Get schedule with given ID."""

    # GET data
    res = requests.get(
        f"{base_url}/{api_version}/sensors/{sensor_id}/schedules/{schedule_id}",
        json={
            "duration": pd.Timedelta(duration).isoformat(),  # for example: PT1H
        },
        headers={"Authorization": auth_token},
    )
    if res.status_code != 200:
        raise ValueError(f"Request failed with status code {res.status_code} and message: {res.json()}")
    return res.json()


get_response = get_schedule(
    base_url="http://localhost:5000/api",
    auth_token=fm.access_token,
    api_version="v3_0",
    sensor_id=2,
    schedule_id="40ffc830-338d-46d4-844c-cdc635b6e189",
    duration="PT24H",
)
print(get_response)
