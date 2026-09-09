import CryptoKit
import Foundation

enum SessionExporter {
    static func sha256(of url: URL) throws -> String {
        let data = try Data(contentsOf: url)
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    static func writeJSON(from store: SessionStore) throws -> URL {
        var files: [CaptureSessionDocument.FileEntry] = []
        for still in store.stills {
            let rel = still.url.path.replacingOccurrences(of: store.root.path + "/", with: "")
                .replacingOccurrences(of: "\\", with: "/")
            files.append(.init(
                path: rel,
                bytes: try still.url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0,
                sha256: try sha256(of: still.url),
                role: "still",
                cameraGroup: still.cameraGroup
            ))
        }
        if let video = store.videoURL {
            let rel = video.path.replacingOccurrences(of: store.root.path + "/", with: "")
                .replacingOccurrences(of: "\\", with: "/")
            files.append(.init(
                path: rel,
                bytes: try video.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0,
                sha256: try sha256(of: video),
                role: "video",
                cameraGroup: nil
            ))
        }
        let document = store.document(files: files)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        let data = try encoder.encode(document)
        let url = store.root.appendingPathComponent("capture.json")
        try data.write(to: url, options: .atomic)
        return url
    }

    static func zipURL(for store: SessionStore) -> URL {
        FileManager.default.temporaryDirectory
            .appendingPathComponent("vitrine-\(store.sessionId.uuidString.lowercased()).zip")
    }
}
