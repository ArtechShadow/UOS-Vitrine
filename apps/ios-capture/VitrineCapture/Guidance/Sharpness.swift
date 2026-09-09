import CoreImage
import UIKit

enum Sharpness {
    /// Variance of the Laplacian on a downscaled luma image — the same idea as
    /// `vitrine.ingest.sharpness`. Absolute values are only compared on-device.
    static func score(_ data: Data, workingLongEdge: CGFloat = 800) -> Double {
        guard let image = UIImage(data: data), let cg = image.cgImage else { return 0 }
        let longest = max(CGFloat(cg.width), CGFloat(cg.height))
        let scale = longest > workingLongEdge ? workingLongEdge / longest : 1
        let size = CGSize(width: CGFloat(cg.width) * scale, height: CGFloat(cg.height) * scale)
        let format = UIGraphicsImageRendererFormat.default()
        format.scale = 1
        let renderer = UIGraphicsImageRenderer(size: size, format: format)
        let scaled = renderer.image { _ in
            UIImage(cgImage: cg).draw(in: CGRect(origin: .zero, size: size))
        }
        guard let grey = scaled.cgImage else { return 0 }
        let ci = CIImage(cgImage: grey)
        let laplacian: [CGFloat] = [0, 1, 0, 1, -4, 1, 0, 1, 0]
        let filter = CIFilter(name: "CIConvolution3X3")
        filter?.setValue(ci, forKey: kCIInputImageKey)
        filter?.setValue(CIVector(values: laplacian, count: 9), forKey: "inputWeights")
        filter?.setValue(0.0, forKey: "inputBias")
        guard let output = filter?.outputImage else { return 0 }
        let context = CIContext(options: [.useSoftwareRenderer: true])
        guard let raw = context.createCGImage(output, from: output.extent) else { return 0 }
        return variance(of: raw)
    }

    private static func variance(of image: CGImage) -> Double {
        let width = image.width
        let height = image.height
        var pixels = [UInt8](repeating: 0, count: width * height)
        guard let ctx = CGContext(
            data: &pixels,
            width: width,
            height: height,
            bitsPerComponent: 8,
            bytesPerRow: width,
            space: CGColorSpaceCreateDeviceGray(),
            bitmapInfo: CGImageAlphaInfo.none.rawValue
        ) else { return 0 }
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
        let values = pixels.map { Double($0) }
        let mean = values.reduce(0, +) / Double(max(values.count, 1))
        let varSum = values.reduce(0) { $0 + ($1 - mean) * ($1 - mean) }
        return varSum / Double(max(values.count, 1))
    }
}
