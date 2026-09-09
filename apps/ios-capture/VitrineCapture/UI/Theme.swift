import SwiftUI

enum VitrineTheme {
    static let orange = Color(red: 1.0, green: 0.416, blue: 0.0)
    static let cream = Color(red: 0.957, green: 0.949, blue: 0.933)
    static let muted = Color(red: 0.604, green: 0.588, blue: 0.549)
    static let bg = Color(red: 0.047, green: 0.047, blue: 0.055)
    static let panel = Color(red: 0.094, green: 0.094, blue: 0.110)
    static let green = Color(red: 0.243, green: 0.812, blue: 0.557)
    static let amber = Color(red: 0.910, green: 0.722, blue: 0.290)

    static let stillsSuggestion: [RoomSize: Int] = [
        .small: 48,
        .room: 72,
        .large: 120,
    ]
}

enum RoomSize: String, Codable, CaseIterable, Identifiable {
    case small, room, large
    var id: String { rawValue }
    var label: String {
        switch self {
        case .small: return "Small interior"
        case .room: return "Room (~4 × 5 m)"
        case .large: return "Large gallery"
        }
    }
}

enum CapturePass: String, Codable, CaseIterable, Identifiable {
    case orbitChest = "orbit_chest"
    case orbitKnee = "orbit_knee"
    case loopClose = "loop_close"
    case detail
    case video
    var id: String { rawValue }
    var title: String {
        switch self {
        case .orbitChest: return "Chest orbit"
        case .orbitKnee: return "Knee orbit"
        case .loopClose: return "Close the loop"
        case .detail: return "Detail pass"
        case .video: return "Video glue"
        }
    }
    var coaching: String {
        switch self {
        case .orbitChest: return "Walk the perimeter. Move a pace between shots. Overlap edges."
        case .orbitKnee: return "Same orbit, lower. Photograph floor–wall junctions."
        case .loopClose: return "Re-shoot the first few positions so the walk meets itself."
        case .detail: return "Fill the frame on text, instruments, anything that must be readable."
        case .video: return "Move slower than feels natural. Do not zoom. Pause at corners."
        }
    }
}

enum ScreensPolicy: String, Codable, CaseIterable, Identifiable {
    case off, paused, playing
    var id: String { rawValue }
    var label: String {
        switch self {
        case .off: return "Screens off"
        case .paused: return "Paused on a frame"
        case .playing: return "Playing (accept artefacts)"
        }
    }
}

enum MirrorsPolicy: String, Codable, CaseIterable, Identifiable {
    case covered, accepted
    case maskLater = "mask-later"
    var id: String { rawValue }
    var label: String {
        switch self {
        case .covered: return "Covered during capture"
        case .accepted: return "Accepted and documented"
        case .maskLater: return "Mask later"
        }
    }
}
