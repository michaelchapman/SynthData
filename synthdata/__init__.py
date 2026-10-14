"""Generate synthetic health data from dataset metadata and summary statistics."""

from .generate import generate, write_output
from .profile import PROFILE_VERSION, load_profile
from .profiler import profile_package

__all__ = ["generate", "write_output", "load_profile", "profile_package", "PROFILE_VERSION"]
__version__ = "0.1.0"
