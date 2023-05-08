import sys
from importlib.metadata import PackageNotFoundError, version  # pragma: no cover

from flexmeasures_client.client import FlexMeasuresClient

try:
    # Change here if project is renamed and does not equal the package name
    dist_name = "flexmeasures-client"
    __version__ = version(dist_name)
except PackageNotFoundError:  # pragma: no cover
    __version__ = "unknown"
finally:
    del version, PackageNotFoundError
