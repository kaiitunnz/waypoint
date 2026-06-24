import importlib.metadata
from importlib.metadata import PackageNotFoundError

__all__ = ["__version__"]

try:
    __version__ = importlib.metadata.version("waypoint")
except PackageNotFoundError:
    __version__ = "0.0.0"
