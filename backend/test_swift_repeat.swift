import Foundation

let bundle = Bundle(path: "/System/Library/PrivateFrameworks/MobileTimer.framework")
bundle?.load()

let args = CommandLine.arguments
let repeatSchedule = args.count > 1 ? Int(args[1]) ?? 0 : 0

guard let AlarmManagerClass = NSClassFromString("MTAlarmManager") as? NSObject.Type,
      let MutableAlarmClass = NSClassFromString("MTMutableAlarm") as? NSObject.Type else {
    exit(1)
}

AlarmManagerClass.perform(Selector(("warmUp")))
let manager = AlarmManagerClass.init()
manager.perform(Selector(("checkIn")))

RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.5))

let alarm = MutableAlarmClass.init()
alarm.setValue(3, forKey: "hour")
alarm.setValue(45, forKey: "minute")
alarm.setValue("Swift Repeat Test", forKey: "title")
alarm.setValue(true, forKey: "enabled")
if repeatSchedule > 0 {
    alarm.setValue(repeatSchedule, forKey: "repeatSchedule")
}

manager.perform(Selector(("addAlarm:")), with: alarm)
RunLoop.main.run(until: Date(timeIntervalSinceNow: 2.0))
print("Added alarm with repeatSchedule=\(repeatSchedule)")
