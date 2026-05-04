import Foundation

let bundle = Bundle(path: "/System/Library/PrivateFrameworks/MobileTimer.framework")
bundle?.load()

guard let AlarmManagerClass = NSClassFromString("MTAlarmManager") as? NSObject.Type else {
    exit(1)
}
AlarmManagerClass.perform(Selector(("warmUp")))
let manager = AlarmManagerClass.init()
manager.perform(Selector(("checkIn")))

RunLoop.main.run(until: Date(timeIntervalSinceNow: 0.5))

let group = DispatchGroup()
group.enter()

let sel = NSSelectorFromString("alarmsSync")
if manager.responds(to: sel) {
    let unmanaged = manager.perform(sel)
    if let alarms = unmanaged?.takeUnretainedValue() as? [NSObject] {
        for alarm in alarms {
            let title = alarm.value(forKey: "title") as? String ?? ""
            if title == "Monday Test" {
                let repeatSchedule = alarm.value(forKey: "repeatSchedule") as? Int ?? 0
                print("Found: \(title), repeatSchedule=\(repeatSchedule)")
            }
        }
    }
}
