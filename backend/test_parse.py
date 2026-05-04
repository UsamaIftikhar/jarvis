import sys
import re
from datetime import datetime, timedelta

raw = "every tuesday at 1 am"

_DAY_BITS = {
    "sunday": 1, "sun": 1,
    "monday": 2, "mon": 2,
    "tuesday": 4, "tue": 4, "tues": 4,
    "wednesday": 8, "wed": 8,
    "thursday": 16, "thu": 16, "thur": 16, "thurs": 16,
    "friday": 32, "fri": 32,
    "saturday": 64, "sat": 64,
}

cleaned = raw.lower()
_day_pattern = r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b"
cleaned = re.sub(_day_pattern, "", cleaned)
cleaned = re.sub(r"\b(at|on|for|only|each|every)\b", "", cleaned)
cleaned = re.sub(r"\s+", " ", cleaned).strip()
print("Cleaned:", cleaned)
