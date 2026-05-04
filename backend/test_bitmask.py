from datetime import datetime
import re

combined = "every tuesday at 1 am jarvis alarm"
_DAY_BITS = {
    "sunday": 1, "sun": 1,
    "monday": 2, "mon": 2,
    "tuesday": 4, "tue": 4, "tues": 4,
    "wednesday": 8, "wed": 8,
    "thursday": 16, "thu": 16, "thur": 16, "thurs": 16,
    "friday": 32, "fri": 32,
    "saturday": 64, "sat": 64,
}

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

print(f"Repeat: {repeat}, Desc: {repeat_desc}")
