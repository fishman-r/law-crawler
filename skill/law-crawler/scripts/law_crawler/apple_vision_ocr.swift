import Foundation
import Vision
import AppKit

let request = VNRecognizeTextRequest()
request.recognitionLevel = .accurate
request.usesLanguageCorrection = true
let supported = try request.supportedRecognitionLanguages()
let metadata: [String: Any] = ["engine": "apple-vision", "os_version": ProcessInfo.processInfo.operatingSystemVersionString, "revision": request.revision, "supported_languages": supported]
if CommandLine.arguments.count == 2 && CommandLine.arguments[1] == "--probe" {
    print(String(data: try JSONSerialization.data(withJSONObject: metadata, options: [.sortedKeys]), encoding: .utf8)!)
} else {
    guard CommandLine.arguments.count >= 3 else { fatalError("Expected image path and comma-separated languages") }
    let languages = CommandLine.arguments[2].split(separator: ",").map(String.init)
    guard languages.allSatisfy({ supported.contains($0) }) else { fatalError("Unsupported recognition language") }
    request.recognitionLanguages = languages
    let start = Date()
    let url = URL(fileURLWithPath: CommandLine.arguments[1])
    try VNImageRequestHandler(url: url).perform([request])
    let observations = (request.results ?? []).compactMap { observation -> [String: Any]? in
        guard let candidate = observation.topCandidates(1).first else { return nil }
        let b = observation.boundingBox
        return ["text": candidate.string, "confidence": candidate.confidence, "bbox": [b.minX, 1 - b.maxY, b.width, b.height]]
    }
    var result = metadata
    result["languages"] = languages
    result["bbox_coordinate_system"] = "normalized_top_left_xywh"
    result["observations"] = observations
    result["duration_seconds"] = Date().timeIntervalSince(start)
    print(String(data: try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys]), encoding: .utf8)!)
}
