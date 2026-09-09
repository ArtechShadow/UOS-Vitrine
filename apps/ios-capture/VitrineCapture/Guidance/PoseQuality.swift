import CoreMotion
import Foundation
import simd

enum PoseChip: String {
    case good = "Good Pose · Steady"
    case tooFast = "Too fast"
    case tooSimilar = "Too similar"
    case tooFar = "Too far"
    case unlocked = "Lock lights first"
}

struct PoseSample {
    var translation: SIMD3<Float>
    var timestamp: TimeInterval
}

final class PoseQuality {
    private let motion = CMMotionManager()
    private var lastAccepted: PoseSample?
    private var gyro: Double = 0

    /// Metres. Below this, the new still is treated as a duplicate of the last.
    var similarMetres: Float = 0.25
    /// Metres. Above this, the camera is too far from the last still for overlap.
    var farMetres: Float = 2.4
    var gyroLimit: Double = 0.35
    var sharpnessFloor: Double = 18

    func start() {
        guard motion.isGyroAvailable else { return }
        motion.gyroUpdateInterval = 1.0 / 30.0
        motion.startGyroUpdates(to: .main) { [weak self] data, _ in
            guard let data else { return }
            let g = data.rotationRate
            self?.gyro = sqrt(g.x * g.x + g.y * g.y + g.z * g.z)
        }
    }

    func stop() { motion.stopGyroUpdates() }

    func evaluate(translation: SIMD3<Float>, sharpness: Double, locked: Bool) -> PoseChip {
        if !locked { return .unlocked }
        if gyro > gyroLimit { return .tooFast }
        if sharpness < sharpnessFloor { return .tooFast }
        if let last = lastAccepted {
            let delta = simd_length(translation - last.translation)
            // ARKit translations are wired in a later pass. Until then both
            // samples may be origin and spatial overlap cannot be judged.
            if simd_length(translation) > 0 || simd_length(last.translation) > 0 {
                if delta < similarMetres { return .tooSimilar }
                if delta > farMetres { return .tooFar }
            }
        }
        return .good
    }

    func accept(translation: SIMD3<Float>) {
        lastAccepted = PoseSample(translation: translation, timestamp: Date().timeIntervalSince1970)
    }
}
