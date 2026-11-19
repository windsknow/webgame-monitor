// 从本地视频文件均匀抽 N 帧存成 JPG（每周核验时用，不进定时任务）。
// swift scripts/frames.swift <视频.mp4> <输出目录> [帧数=8]
import AVFoundation
import AppKit

// 解释器模式下参数前面会带一串编译器参数，用户参数在 "--" 之后
let raw = CommandLine.arguments
let a = raw.contains("--") ? [raw[0]] + Array(raw.drop(while: { $0 != "--" }).dropFirst()) : raw
guard a.count >= 3 else { print("用法: swift frames.swift <视频> <输出目录> [帧数]"); exit(1) }
let n = a.count > 3 ? Int(a[3]) ?? 8 : 8
let asset = AVURLAsset(url: URL(fileURLWithPath: a[1]))
let gen = AVAssetImageGenerator(asset: asset)
gen.appliesPreferredTrackTransform = true
gen.maximumSize = CGSize(width: 960, height: 960)
gen.requestedTimeToleranceBefore = .zero
gen.requestedTimeToleranceAfter = .zero
let sem = DispatchSemaphore(value: 0)
asset.loadValuesAsynchronously(forKeys: ["duration", "tracks"]) { sem.signal() }
sem.wait()
let dur = CMTimeGetSeconds(asset.duration)
guard dur > 0 else { print("读不到时长"); exit(2) }
try? FileManager.default.createDirectory(atPath: a[2], withIntermediateDirectories: true)
for i in 0..<n {
    let t = dur * (Double(i) + 0.5) / Double(n)
    guard let cg = try? gen.copyCGImage(at: CMTime(seconds: t, preferredTimescale: 600), actualTime: nil) else { continue }
    let rep = NSBitmapImageRep(cgImage: cg)
    if let d = rep.representation(using: .jpeg, properties: [.compressionFactor: 0.8]) {
        try? d.write(to: URL(fileURLWithPath: String(format: "%@/f%02d_%05.1fs.jpg", a[2], i, t)))
    }
}
print(String(format: "时长 %.1f 秒，抽 %d 帧", dur, n))
