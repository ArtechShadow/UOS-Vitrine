import Foundation

/// On-disk contract `vitrine/capture-session/1`. Keep field names in lockstep
/// with `vitrine/capture_session.py` and `docs/capture-session.md`.
struct CaptureSessionDocument: Codable, Equatable {
    static let schema = "vitrine/capture-session/1"

    struct Device: Codable, Equatable {
        var model: String
        var os: String
        var lidarAvailable: Bool
        var cameras: [String]
        enum CodingKeys: String, CodingKey {
            case model, os
            case lidarAvailable = "lidar_available"
            case cameras
        }
    }

    struct LockedSettings: Codable, Equatable {
        var ae: Bool
        var awb: Bool
        var af: Bool
        var flash: String
        var zoom: Double
        var iso: Double?
        var shutter: Double?
    }

    struct Pass: Codable, Equatable {
        var type: String
        var stillCount: Int
        var startedAt: String?
        var endedAt: String?
        enum CodingKeys: String, CodingKey {
            case type
            case stillCount = "still_count"
            case startedAt = "started_at"
            case endedAt = "ended_at"
        }
    }

    struct Checklist: Codable, Equatable {
        var threeOrMoreViews: Bool
        var twoHeights: Bool
        var loopClosed: Bool
        var detailPass: Bool
        var corners: Bool
        var floorEdges: Bool
        var exposureLocked: Bool
        enum CodingKeys: String, CodingKey {
            case threeOrMoreViews = "three_or_more_views"
            case twoHeights = "two_heights"
            case loopClosed = "loop_closed"
            case detailPass = "detail_pass"
            case corners
            case floorEdges = "floor_edges"
            case exposureLocked = "exposure_locked"
        }
    }

    struct Coverage: Codable, Equatable {
        var stillsCount: Int
        var stillsTarget: Int
        var overlapEstimate: Double?
        var lidarUsed: Bool
        var holes: [String]
        enum CodingKeys: String, CodingKey {
            case stillsCount = "stills_count"
            case stillsTarget = "stills_target"
            case overlapEstimate = "overlap_estimate"
            case lidarUsed = "lidar_used"
            case holes
        }
    }

    struct FileEntry: Codable, Equatable {
        var path: String
        var bytes: Int
        var sha256: String
        var role: String
        var cameraGroup: String?
        enum CodingKeys: String, CodingKey {
            case path, bytes, sha256, role
            case cameraGroup = "camera_group"
        }
    }

    var schema: String
    var sessionId: String
    var title: String
    var venue: String?
    var subject: String?
    var createdAt: String
    var closedAt: String?
    var device: Device
    var lockedSettings: LockedSettings
    var screensPolicy: String
    var mirrorsPolicy: String
    var roomSize: String
    var passes: [Pass]
    var checklist: Checklist
    var coverage: Coverage
    var files: [FileEntry]

    enum CodingKeys: String, CodingKey {
        case schema
        case sessionId = "session_id"
        case title, venue, subject
        case createdAt = "created_at"
        case closedAt = "closed_at"
        case device
        case lockedSettings = "locked_settings"
        case screensPolicy = "screens_policy"
        case mirrorsPolicy = "mirrors_policy"
        case roomSize = "room_size"
        case passes, checklist, coverage, files
    }
}

struct AcceptedStill: Identifiable, Equatable {
    var id: UUID
    var url: URL
    var cameraGroup: String
    var pass: CapturePass
    var sharpness: Double
    var createdAt: Date
}

struct RejectedStill: Identifiable, Equatable {
    var id: UUID
    var url: URL
    var reason: String
}
