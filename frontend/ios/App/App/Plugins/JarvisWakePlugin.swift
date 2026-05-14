import AVFoundation
import Capacitor
import Foundation
import Speech

/**
 JarvisWakePlugin — always-on "Hey JARVIS" wake word for iOS.

 Strategy:
  1. AVAudioSession declared as .playAndRecord with background audio — keeps the app
     alive even when the screen is off (iOS allows background audio apps).
  2. A silent PCM buffer loops via AVAudioPlayerNode — this is the "trick" that
     convinces iOS the app is an active audio app so it stays in background.
  3. AVAudioEngine + SFSpeechRecognizer run continuous on-device speech recognition.
     When any configured phrase is detected the "wakeWord" event fires to the web layer.

 Battery note: SFSpeechRecognizer on-device recognition uses the Neural Engine and is
 significantly lighter than cloud recognition. The silent audio loop adds ~0.5% CPU.
 */
@objc(JarvisWakePlugin)
public class JarvisWakePlugin: CAPPlugin, CAPBridgedPlugin {

    public let identifier  = "JarvisWakePlugin"
    public let jsName      = "JarvisWakePlugin"
    public let pluginMethods: [CAPPluginMethod] = [
        CAPPluginMethod(name: "startListening", returnType: CAPPluginReturnPromise),
        CAPPluginMethod(name: "stopListening",  returnType: CAPPluginReturnPromise),
    ]

    // Audio engine + nodes
    private let audioEngine  = AVAudioEngine()
    private let playerNode   = AVAudioPlayerNode()
    private var silentBuffer: AVAudioPCMBuffer?

    // Speech recognition
    private var recognizer:   SFSpeechRecognizer?
    private var recognitionRequest:  SFSpeechAudioBufferRecognitionRequest?
    private var recognitionTask:     SFSpeechRecognitionTask?

    // Configuration
    private var watchPhrases: [String] = ["hey jarvis", "jarvis", "hey javis", "hey travis", "wake up jarvis"]
    private var isRunning = false

    // MARK: – Public API

    @objc func startListening(_ call: CAPPluginCall) {
        if let phrases = call.getArray("phrases") as? [String], !phrases.isEmpty {
            watchPhrases = phrases.map { $0.lowercased() }
        }
        requestPermissions { [weak self] granted in
            guard let self else { return }
            if !granted {
                call.reject("Microphone or speech recognition permission denied")
                return
            }
            DispatchQueue.main.async {
                do {
                    try self.startEngine()
                    call.resolve()
                } catch {
                    call.reject("Failed to start audio engine: \(error.localizedDescription)")
                }
            }
        }
    }

    @objc func stopListening(_ call: CAPPluginCall) {
        stopEngine()
        call.resolve()
    }

    // MARK: – Permissions

    private func requestPermissions(completion: @escaping (Bool) -> Void) {
        SFSpeechRecognizer.requestAuthorization { authStatus in
            guard authStatus == .authorized else { completion(false); return }
            if #available(iOS 17.0, *) {
                AVAudioApplication.requestRecordPermission { granted in
                    completion(granted)
                }
            } else {
                AVAudioSession.sharedInstance().requestRecordPermission { granted in
                    completion(granted)
                }
            }
        }
    }

    // MARK: – Audio engine

    private func startEngine() throws {
        guard !isRunning else { return }

        let session = AVAudioSession.sharedInstance()
        try session.setCategory(.playAndRecord,
                                mode: .default,
                                options: [.defaultToSpeaker, .allowBluetooth, .mixWithOthers])
        try session.setActive(true, options: .notifyOthersOnDeactivation)

        // Build a 1-second silent PCM buffer at the hardware sample rate
        let format = AVAudioFormat(standardFormatWithSampleRate: 44100, channels: 1)!
        let frameCount = AVAudioFrameCount(44100)
        silentBuffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frameCount)
        silentBuffer!.frameLength = frameCount
        // Buffer is already zeroed by default — silence.

        audioEngine.attach(playerNode)
        audioEngine.connect(playerNode,
                            to: audioEngine.mainMixerNode,
                            format: format)

        // Loop the silent buffer forever to keep iOS background audio alive
        playerNode.scheduleBuffer(silentBuffer!,
                                  at: nil,
                                  options: .loops,
                                  completionHandler: nil)

        // Tap the input node for speech recognition
        let inputNode = audioEngine.inputNode
        let inputFormat = inputNode.outputFormat(forBus: 0)

        recognizer = SFSpeechRecognizer(locale: Locale(identifier: "en-US"))
        recognizer?.defaultTaskHint = .dictation

        recognitionRequest = SFSpeechAudioBufferRecognitionRequest()
        recognitionRequest!.shouldReportPartialResults = true
        recognitionRequest!.requiresOnDeviceRecognition = true  // no network needed

        recognitionTask = recognizer?.recognitionTask(with: recognitionRequest!) { [weak self] result, error in
            guard let self else { return }
            if let result {
                let transcript = result.bestTranscription.formattedString.lowercased()
                for phrase in self.watchPhrases {
                    if transcript.contains(phrase) {
                        DispatchQueue.main.async {
                            self.notifyListeners("wakeWord", data: ["phrase": phrase])
                        }
                        // Restart recognition so we don't keep reporting the same hit
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                            self.restartRecognition()
                        }
                        return
                    }
                }
            }
            // Restart on error or session end (recognition sessions have a ~1 min limit)
            if error != nil || (result?.isFinal ?? false) {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                    self.restartRecognition()
                }
            }
        }

        inputNode.installTap(onBus: 0, bufferSize: 1024, format: inputFormat) { [weak self] buffer, _ in
            self?.recognitionRequest?.append(buffer)
        }

        audioEngine.prepare()
        try audioEngine.start()
        playerNode.play()
        isRunning = true

        // Listen for audio interruptions (calls, Siri, etc.) and resume
        NotificationCenter.default.addObserver(
            self,
            selector: #selector(handleAudioInterruption),
            name: AVAudioSession.interruptionNotification,
            object: nil
        )
    }

    private func stopEngine() {
        guard isRunning else { return }
        isRunning = false
        NotificationCenter.default.removeObserver(self, name: AVAudioSession.interruptionNotification, object: nil)
        audioEngine.inputNode.removeTap(onBus: 0)
        recognitionTask?.cancel()
        recognitionTask = nil
        recognitionRequest?.endAudio()
        recognitionRequest = nil
        playerNode.stop()
        audioEngine.stop()
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private func restartRecognition() {
        guard isRunning else { return }
        audioEngine.inputNode.removeTap(onBus: 0)
        recognitionTask?.cancel()
        recognitionTask = nil
        recognitionRequest?.endAudio()
        recognitionRequest = nil

        let inputNode = audioEngine.inputNode
        let inputFormat = inputNode.outputFormat(forBus: 0)
        recognitionRequest = SFSpeechAudioBufferRecognitionRequest()
        recognitionRequest!.shouldReportPartialResults = true
        recognitionRequest!.requiresOnDeviceRecognition = true

        recognitionTask = recognizer?.recognitionTask(with: recognitionRequest!) { [weak self] result, error in
            guard let self else { return }
            if let result {
                let transcript = result.bestTranscription.formattedString.lowercased()
                for phrase in self.watchPhrases {
                    if transcript.contains(phrase) {
                        DispatchQueue.main.async {
                            self.notifyListeners("wakeWord", data: ["phrase": phrase])
                        }
                        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) {
                            self.restartRecognition()
                        }
                        return
                    }
                }
            }
            if error != nil || (result?.isFinal ?? false) {
                DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) {
                    self.restartRecognition()
                }
            }
        }

        inputNode.installTap(onBus: 0, bufferSize: 1024, format: inputFormat) { [weak self] buffer, _ in
            self?.recognitionRequest?.append(buffer)
        }
    }

    // MARK: – Interruption handling

    @objc private func handleAudioInterruption(notification: Notification) {
        guard let info = notification.userInfo,
              let typeValue = info[AVAudioSessionInterruptionTypeKey] as? UInt,
              let type = AVAudioSession.InterruptionType(rawValue: typeValue) else { return }

        if type == .ended {
            // Resume after a call or Siri interruption
            DispatchQueue.main.asyncAfter(deadline: .now() + 1.0) { [weak self] in
                guard let self, self.isRunning else { return }
                try? self.audioEngine.start()
                self.playerNode.play()
                self.restartRecognition()
            }
        }
    }
}
