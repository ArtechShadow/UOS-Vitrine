import Foundation
import simd

/// Coarse coverage accumulator used until ARKit mesh painting is wired on a Mac.
/// Each accepted still records a camera position; holes are reported as regions
/// with no nearby sample. This is guidance, not a COLMAP pose.
struct CoverageMap {
    var samples: [SIMD3<Float>] = []

    mutating func add(_ translation: SIMD3<Float>) {
        samples.append(translation)
    }

    var twoHeights: Bool {
        guard samples.count >= 4 else { return false }
        let ys = samples.map(\.y)
        guard let minY = ys.min(), let maxY = ys.max() else { return false }
        return (maxY - minY) > 0.35
    }

    func loopClosed(tolerance: Float = 0.6) -> Bool {
        guard let first = samples.first, let last = samples.last, samples.count > 8 else { return false }
        return simd_length(first - last) < tolerance
    }
}
