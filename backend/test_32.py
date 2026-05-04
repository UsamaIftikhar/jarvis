from datetime import datetime, timezone
import subprocess, uuid, plistlib

entry = {
    "$MTAlarm": {
        "MTAlarmAllowsSnooze": True,
        "MTAlarmBedtimeDismissAction": 0,
        "MTAlarmBedtimeDoNotDisturb": False,
        "MTAlarmBedtimeDoNotDisturbOptions": 0,
        "MTAlarmBedtimeHour": 0,
        "MTAlarmBedtimeMinute": 0,
        "MTAlarmDataVersion": 3.0,
        "MTAlarmDismissAction": 0,
        "MTAlarmEnabled": True,
        "MTAlarmHour": 3,
        "MTAlarmID": str(uuid.uuid4()).upper(),
        "MTAlarmIsSleep": False,
        "MTAlarmLastModifiedDate": datetime.now(timezone.utc).replace(tzinfo=None),
        "MTAlarmMinute": 0,
        "MTAlarmOnboardingVersion": 0,
        "MTAlarmRepeatSchedule": 32,
        "MTAlarmSleepScheduleKey": False,
        "MTAlarmSound": {
            "$MTSound": {"MTSoundToneID": "system:Radial", "MTSoundType": 2}
        },
        "MTAlarmTimeInBedTrackingKey": False,
    }
}

export = subprocess.run(["defaults", "export", "com.apple.mobiletimerd", "-"], capture_output=True, timeout=5)
data = plistlib.loads(export.stdout)
container = data.setdefault("MTAlarms", {"MTAlarms": [], "MTSleepAlarms": []})
container.setdefault("MTAlarms", []).append(entry)
xml_bytes = plistlib.dumps(data, fmt=plistlib.FMT_XML)
subprocess.run(["defaults", "import", "com.apple.mobiletimerd", "-"], input=xml_bytes, capture_output=True, timeout=5)

pid = subprocess.run(["pgrep", "mobiletimerd"], capture_output=True, text=True).stdout.strip()
if pid:
    subprocess.run(["kill", "-9", pid], capture_output=True, timeout=5)
