// ocr_frames.swift — Apple Vision OCR (ücretsiz, cihaz içi, tr-TR + en-US)
// Kullanım: ocr_frames <görsel1> <görsel2> ...
// Çıktı: görsel başına bir satır JSON: {"file":"...","text":"..."}
import Foundation
import Vision
import ImageIO

struct Row: Codable {
    let file: String
    let text: String
}

let args = Array(CommandLine.arguments.dropFirst())
if args.isEmpty {
    FileHandle.standardError.write(Data("usage: ocr_frames <image> [image ...]\n".utf8))
    exit(1)
}

let encoder = JSONEncoder()
let stdout = FileHandle.standardOutput

for path in args {
    var text = ""
    if let src = CGImageSourceCreateWithURL(URL(fileURLWithPath: path) as CFURL, nil),
       let cg = CGImageSourceCreateImageAtIndex(src, 0, nil) {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate          // revision 3: tr-TR + en-US birlikte doğrulandı
        request.usesLanguageCorrection = true
        request.recognitionLanguages = ["tr-TR", "en-US"]
        let handler = VNImageRequestHandler(cgImage: cg)
        try? handler.perform([request])
        text = (request.results ?? []).compactMap { $0.topCandidates(1).first?.string }.joined(separator: "\n")
    } else {
        FileHandle.standardError.write(Data("UYARI: okunamadi: \(path)\n".utf8))
    }
    let data = (try! encoder.encode(Row(file: path, text: text))) + Data("\n".utf8)
    stdout.write(data)
}
exit(0)
