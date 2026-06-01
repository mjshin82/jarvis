// audiod.swift — AVAudioEngine + VoiceProcessingIO 오디오 데몬
import AVFoundation
import Foundation

let MIC: UInt8 = 1, EVENT: UInt8 = 2, PLAY_VOICE: UInt8 = 3
let FLUSH_VOICE: UInt8 = 4, PLAY_MUSIC: UInt8 = 5, STOP_MUSIC: UInt8 = 6
let MIC_SR = 16000.0, PLAY_SR = 48000.0

let stdoutFH = FileHandle.standardOutput
let outLock = NSLock()

func send(_ type: UInt8, _ payload: Data) {
    var header = Data([type])
    var len = UInt32(payload.count).littleEndian
    header.append(Data(bytes: &len, count: 4))
    outLock.lock(); stdoutFH.write(header); stdoutFH.write(payload); outLock.unlock()
}
func sendEvent(_ json: String) { send(EVENT, json.data(using: .utf8)!) }

final class Audiod {
    let engine = AVAudioEngine()
    var voice = AVAudioPlayerNode()
    var music = AVAudioPlayerNode()
    let playFmt = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: PLAY_SR,
                                channels: 1, interleaved: false)!
    let micFmt = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: MIC_SR,
                               channels: 1, interleaved: false)!
    var voiceCompleted = 0
    let pendLock = NSLock()

    func start() throws {
        let input = engine.inputNode
        try input.setVoiceProcessingEnabled(true)
        let inFmt = input.outputFormat(forBus: 0)
        let conv = AVAudioConverter(from: inFmt, to: micFmt)!

        engine.attach(voice); engine.attach(music)
        engine.connect(voice, to: engine.mainMixerNode, format: playFmt)
        engine.connect(music, to: engine.mainMixerNode, format: playFmt)

        input.installTap(onBus: 0, bufferSize: 1024, format: inFmt) { buf, _ in
            let ratio = MIC_SR / inFmt.sampleRate
            let cap = AVAudioFrameCount(Double(buf.frameLength) * ratio + 64)
            guard let out = AVAudioPCMBuffer(pcmFormat: self.micFmt, frameCapacity: cap)
            else { return }
            var err: NSError?
            var fed = false
            conv.convert(to: out, error: &err) { _, status in
                if fed { status.pointee = .noDataNow; return nil }
                fed = true; status.pointee = .haveData; return buf
            }
            if let ch = out.floatChannelData {
                let n = Int(out.frameLength)
                send(MIC, Data(bytes: ch[0], count: n * 4))
            }
        }
        try engine.start()
        voice.play(); music.play()
    }

    func makeBuffer(_ pcm: [Float]) -> AVAudioPCMBuffer? {
        guard let b = AVAudioPCMBuffer(pcmFormat: playFmt,
              frameCapacity: AVAudioFrameCount(max(pcm.count, 1))) else { return nil }
        b.frameLength = AVAudioFrameCount(pcm.count)
        pcm.withUnsafeBufferPointer { src in
            b.floatChannelData![0].update(from: src.baseAddress!, count: pcm.count)
        }
        return b
    }
    func scheduleVoice(_ pcm: [Float]) {
        guard let b = makeBuffer(pcm) else { return }
        voice.scheduleBuffer(b) {
            self.pendLock.lock(); self.voiceCompleted += 1; let n = self.voiceCompleted; self.pendLock.unlock()
            sendEvent("{\"vc\":\(n)}")
        }
    }
    func scheduleMusic(_ pcm: [Float]) {
        guard let b = makeBuffer(pcm) else { return }
        music.scheduleBuffer(b, completionHandler: nil)
    }
    func flushVoice() {
        voice.stop(); voice.play()
    }
    func stopMusic() { music.stop(); music.play() }
}

func bytesToFloats(_ d: Data) -> [Float] {
    var out = [Float](repeating: 0, count: d.count / 4)
    out.withUnsafeMutableBytes { d.copyBytes(to: $0) }
    return out
}

let app = Audiod()
do { try app.start() } catch {
    FileHandle.standardError.write("start error: \(error)\n".data(using: .utf8)!); exit(1)
}

let inFH = FileHandle.standardInput
var buf = Data()
while true {
    let chunk = inFH.availableData
    if chunk.isEmpty { break }
    buf.append(chunk)
    while buf.count >= 5 {
        let type = buf[buf.startIndex]
        let lenBytes = [UInt8](buf.subdata(in: buf.startIndex+1 ..< buf.startIndex+5))
        let len = UInt32(lenBytes[0]) | (UInt32(lenBytes[1])<<8) | (UInt32(lenBytes[2])<<16) | (UInt32(lenBytes[3])<<24)
        if buf.count < 5 + Int(len) { break }
        let payload = buf.subdata(in: buf.startIndex+5 ..< buf.startIndex+5+Int(len))
        buf.removeSubrange(buf.startIndex ..< buf.startIndex+5+Int(len))
        switch type {
        case PLAY_VOICE: app.scheduleVoice(bytesToFloats(payload))
        case FLUSH_VOICE: app.flushVoice()
        case PLAY_MUSIC: app.scheduleMusic(bytesToFloats(payload))
        case STOP_MUSIC: app.stopMusic()
        default: break
        }
    }
}
