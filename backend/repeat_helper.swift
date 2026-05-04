import Foundation

let bundle = Bundle(path: "/System/Library/PrivateFrameworks/MobileTimer.framework")
bundle?.load()

guard let AlarmManagerClass = NSClassFromString("MTAlarmManager") as? NSObject.Type,
      let MutableAlarmClass = NSClassFromString("MTMutableAlarm") as? NSObject.Type else {
    fputs("ERROR\n", stderr); exit(1)
}

AlarmManagerClass.perform(Selector(("warmUp")))
let manager = AlarmManagerClass.init()
manager.perform(Selector(("checkIn")))

RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.5))

let alarm = MutableAlarmClass.init()
alarm.setValue(8, forKey: "hour")
alarm.setValue(0, forKey: "minute")
alarm.setValue("Monday Test", forKey: "title")
alarm.setValue(true, forKey: "enabled")
// set repeat to 2
alarm.setValue(2, forKey: "repeatSchedule")

manager.perform(Selector(("addAlarm:")), with: alarm)

RunLoop.main.run(until: Date(timeIntervalSinceNow: 2.0))
print("Added alarm with repeatSchedule=2")
