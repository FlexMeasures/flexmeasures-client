# HEMS solar example data

`irradiation_data.csv` and `PV_production_data.csv` are **synthetic tutorial
data**, not observations or a validated solar forecast. They cover 21 January
days in Amsterdam local time (`+01:00`) at hourly resolution. The solar-elevation
envelope produces winter daylight hours; a mix of clear, overcast, and changing
cloud profiles makes the days visibly different. PV power is derived from the
same irradiance using a 10 kW nominal array and an illustrative 0.85 performance
ratio. Both HEMS buildings currently receive the same source profile.

During the HEMS data-upload phase, each day's weather label is also posted as a
full-day `label` annotation on each rooftop PV `electricity-production` sensor.
The labels say "Synthetic PV conditions" so they cannot be mistaken for observed
weather. FlexMeasures reuses an identical annotation if the upload is retried.

Regenerate both files together from the repository root:

```sh
python3 examples/HEMS/generate_solar_data.py
```

The generator is deterministic and uses only the Python standard library.
