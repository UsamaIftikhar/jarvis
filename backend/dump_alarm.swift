import Foundation

let bundle = Bundle(path: "/System/Library/PrivateFrameworks/MobileTimer.framework")
bundle?.load()

guard let MutableAlarmClass = NSClassFromString("MTMutableAlarm") as? NSObject.Type else {
    exit(1)
}

let alarm = MutableAlarmClass.init()

var count: UInt32 = 0
let properties = class_copyPropertyList(MutableAlarmClass, &count)
for i in 0..<Int(count) {
    if let property = properties?[i] {
        let name = String(cString: property_getName(property))
        print("Property: \(name)")
    }
}
free(properties)

let ivars = class_copyIvarList(MutableAlarmClass, &count)
for i in 0..<Int(count) {
    if let ivar = ivars?[i] {
        let name = String(cString: ivar_getName(ivar)!)
        print("Ivar: \(name)")
    }
}
free(ivars)
