import re
from datetime import datetime
_DAY_BITS = {
    "monday": 1, "mon": 1,
    "tuesday": 2, "tue": 2, "tues": 2,
    "wednesday": 4, "wed": 4,
    "thursday": 8, "thu": 8, "thur": 8, "thurs": 8,
    "friday": 16, "fri": 16,
    "saturday": 32, "sat": 32,
    "sunday": 64, "sun": 64,
}

combined = "every tuesday at 1:30 am jarvis alarm"
if "every" in combined or "each" in combined or "repeat" in combined:
    day_mask = 0
    day_names = []
    for token in re.findall(r'\b\w+\b', combined):
        if token in _DAY_BITS and not (_DAY_BITS[token] & day_mask):
            day_mask |= _DAY_BITS[token]
            canon = next(k for k, v in _DAY_BITS.items() if v == _DAY_BITS[token] and len(k) > 3)
            day_names.append(canon.capitalize())
    if day_mask:
        repeat = day_mask
        repeat_desc = "every " + "/".join(day_names)
    else:
        repeat = 0
        repeat_desc = "once"
else:
    repeat = 0
    repeat_desc = "once"

print("Repeat:", repeat, "Desc:", repeat_desc)
