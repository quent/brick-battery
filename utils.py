"""
Shared functions and helpers.
"""

import datetime
from math import isnan

def datetime_now():
    """
    Helper function for time based calculations, using local time by default.
    Unambiguous timestamps are useful when used for logging and shared using an API.

    Return a timezone-aware "now" datetime using the system local timezone.
    """
    # Note that while convoluted, this method avoid extra library imports
    # local_tz needs to be evaluated every time as it may change during
    # daylight savings transitions
    local_tz = datetime.datetime.now(datetime.timezone.utc).astimezone().tzinfo
    now = datetime.datetime.now(local_tz)
    return now

def empty_if_nan(value_or_nan):
    """
    Return an empty string if the value passed is not a number.
    This is how missing values are encoded in CSV.
    """
    if isnan(value_or_nan):
        return ''
    return value_or_nan
