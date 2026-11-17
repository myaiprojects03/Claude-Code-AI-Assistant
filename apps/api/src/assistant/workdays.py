"""Working days: weekdays from a rule set, minus public holidays.

Holidays come from the free, open-source `holidays` library. Each rule set (messaging hours,
booking rules, office hours) names its calendar, so staff can change it from Settings:

    {"days": [0, 1, 2, 3, 4], "holidays_country": "GB", "holidays_subdiv": "ENG", ...}

Leaving out `holidays_country` disables holiday handling for that rule set.
"""

from datetime import date
from functools import lru_cache
from typing import Any

import holidays


@lru_cache(maxsize=16)
def _calendar(country: str, subdiv: str | None) -> holidays.HolidayBase:
    # Years are filled in lazily on lookup, so one cached calendar serves every year.
    return holidays.country_holidays(country, subdiv=subdiv)


def holiday_name(d: date, rules: dict[str, Any]) -> str | None:
    country = rules.get("holidays_country")
    if not country:
        return None
    return _calendar(country, rules.get("holidays_subdiv")).get(d)


def is_working_day(d: date, rules: dict[str, Any]) -> bool:
    return d.weekday() in rules["days"] and holiday_name(d, rules) is None
