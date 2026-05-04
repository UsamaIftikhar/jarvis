import Foundation

let bundle = Bundle(path: "/System/Library/PrivateFrameworks/MobileTimer.framework")
bundle?.load()

let args = CommandLine.arguments
guard args.count >= 3 else { fputs("Usage: alarm_helper <hour> <minute> [label]\n", stderr); exit(1) }
let hour   = Int(args[1]) ?? 0
let minute = Int(args[2]) ?? 0
let label  = args.count > 3 ? args[3] : "JARVIS Alarm"

guard let AlarmManagerClass = NSClassFromString("MTAlarmManager") as? NSObject.Type,
      let MutableAlarmClass = NSClassFromString("MTMutableAlarm") as? NSObject.Type else {
    fputs("ERROR: MobileTimer classes not found\n", stderr); exit(2)
}

AlarmManagerClass.perform(Selector(("warmUp")))

let manager = AlarmManagerClass.init()
manager.perform(Selector(("checkIn")))

// Let the run loop spin briefly to establish XPC connection
RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.5))

let alarm = MutableAlarmClass.init()
alarm.setValue(hour,   forKey: "hour")
alarm.setValue(minute, forKey: "minute")
alarm.setValue(label,  forKey: "title")
alarm.setValue(true,   forKey: "enabled")
alarm.setValue(true,   forKey: "allowsSnooze")

manager.perform(Selector(("addAlarm:")), with: alarm)

// Spin the run loop to let XPC deliver the response to mobiletimerd
var saved = false
let deadline = Date(timeIntervalSinceNow: 5.0)
while Date() < deadline {
    RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.5))
    let defaults = UserDefaults(suiteName: "com.apple.mobiletimerd")
    defaults?.synchronize()
    let container = defaults?.dictionary(forKey: "MTAlarms") ?? [:]
    let list = container["MTAlarms"] as? [[String: Any]] ?? []
    if list.contains(where: { item in
        let inner = (item["$MTAlarm"] as? [String: Any]) ?? [:]
        return (inner["MTAlarmHour"] as? Int) == hour &&
               (inner["MTAlarmMinute"] as? Int) == minute
    }) {
        saved = true
        break
    }
}

if saved {
    print("SUCCESS: \(hour):\(String(format:"%02d",minute)) '\(label)'")
    exit(0)
} else {
    fputs("NOT_SAVED: mobiletimerd did not respond\n", stderr)
    exit(3)
}
