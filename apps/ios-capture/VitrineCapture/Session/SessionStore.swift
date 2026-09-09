import ARKit
import Foundation
import UIKit

@MainActor
final class SessionStore: ObservableObject {
    @Published var title = ""
    @Published var venue = ""
    @Published var subject = ""
    @Published var roomSize: RoomSize = .room
    @Published var screensPolicy: ScreensPolicy?
    @Published var mirrorsPolicy: MirrorsPolicy?
    @Published var currentPass: CapturePass = .orbitChest
    @Published var locked = false
    @Published var lidarGuidance = true
    @Published var stills: [AcceptedStill] = []
    @Published var rejected: [RejectedStill] = []
    @Published var videoURL: URL?
    @Published var holes: [String] = []
    @Published var twoHeights = false
    @Published var loopClosed = false
    @Published var detailPassDone = false
    @Published var cornersNoted = false
    @Published var floorEdgesNoted = false

    let sessionId = UUID()
    let createdAt = Date()
    let root: URL

    var stillsTarget: Int { VitrineTheme.stillsSuggestion[roomSize] ?? 72 }
    var canCapture: Bool { locked && screensPolicy != nil && mirrorsPolicy != nil }
    var setupComplete: Bool {
        !title.trimmingCharacters(in: .whitespaces).isEmpty
            && screensPolicy != nil
            && mirrorsPolicy != nil
    }

    init() {
        let base = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        root = base.appendingPathComponent("sessions/\(sessionId.uuidString)", isDirectory: true)
        try? FileManager.default.createDirectory(at: root.appendingPathComponent("stills/wide"), withIntermediateDirectories: true)
        try? FileManager.default.createDirectory(at: root.appendingPathComponent("video"), withIntermediateDirectories: true)
        try? FileManager.default.createDirectory(at: root.appendingPathComponent("sidecar/rejected"), withIntermediateDirectories: true)
    }

    func markPassComplete(_ pass: CapturePass) {
        switch pass {
        case .orbitKnee: twoHeights = true
        case .loopClose: loopClosed = true
        case .detail: detailPassDone = true
        default: break
        }
        if let idx = CapturePass.allCases.firstIndex(of: pass),
           idx + 1 < CapturePass.allCases.count {
            currentPass = CapturePass.allCases[idx + 1]
        }
    }

    func acceptStill(data: Data, sharpness: Double, group: String = "wide") throws -> AcceptedStill {
        let name = String(format: "IMG_%04d.jpg", stills.count + 1)
        let rel = "stills/\(group)/\(name)"
        let url = root.appendingPathComponent(rel)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try data.write(to: url, options: .atomic)
        let still = AcceptedStill(
            id: UUID(),
            url: url,
            cameraGroup: group,
            pass: currentPass,
            sharpness: sharpness,
            createdAt: Date()
        )
        stills.append(still)
        return still
    }

    func rejectStill(data: Data, reason: String) throws {
        let name = String(format: "REJ_%04d.jpg", rejected.count + 1)
        let url = root.appendingPathComponent("sidecar/rejected/\(name)")
        try data.write(to: url, options: .atomic)
        rejected.append(RejectedStill(id: UUID(), url: url, reason: reason))
    }

    func document(files: [CaptureSessionDocument.FileEntry]) -> CaptureSessionDocument {
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime]
        return CaptureSessionDocument(
            schema: CaptureSessionDocument.schema,
            sessionId: sessionId.uuidString.lowercased(),
            title: title,
            venue: venue.isEmpty ? nil : venue,
            subject: subject.isEmpty ? nil : subject,
            createdAt: iso.string(from: createdAt),
            closedAt: iso.string(from: Date()),
            device: .init(
                model: UIDevice.current.model,
                os: "iOS \(UIDevice.current.systemVersion)",
                lidarAvailable: ARWorldTrackingConfiguration.supportsSceneReconstruction(.mesh),
                cameras: Array(Set(stills.map(\.cameraGroup))).sorted()
            ),
            lockedSettings: .init(ae: locked, awb: locked, af: locked, flash: "off", zoom: 1.0, iso: nil, shutter: nil),
            screensPolicy: screensPolicy?.rawValue ?? "paused",
            mirrorsPolicy: mirrorsPolicy?.rawValue ?? "accepted",
            roomSize: roomSize.rawValue,
            passes: CapturePass.allCases.map { pass in
                .init(
                    type: pass.rawValue,
                    stillCount: stills.filter { $0.pass == pass }.count,
                    startedAt: nil,
                    endedAt: nil
                )
            },
            checklist: .init(
                threeOrMoreViews: stills.count >= 3,
                twoHeights: twoHeights,
                loopClosed: loopClosed,
                detailPass: detailPassDone,
                corners: cornersNoted,
                floorEdges: floorEdgesNoted,
                exposureLocked: locked
            ),
            coverage: .init(
                stillsCount: stills.count,
                stillsTarget: stillsTarget,
                overlapEstimate: nil,
                lidarUsed: lidarGuidance,
                holes: holes
            ),
            files: files
        )
    }
}
