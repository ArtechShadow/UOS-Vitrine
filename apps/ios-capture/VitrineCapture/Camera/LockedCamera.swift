import AVFoundation
import UIKit

/// AVFoundation capture that refuses to run until AE/AWB/AF are locked and
/// keeps digital zoom at 1×. Original JPEG bytes are written by the caller.
final class LockedCamera: NSObject, ObservableObject {
    let session = AVCaptureSession()
    @Published var locked = false
    @Published var authorized = false
    @Published var lastError: String?

    private let photo = AVCapturePhotoOutput()
    private var device: AVCaptureDevice?
    private var continuation: CheckedContinuation<Data, Error>?

    func start() async {
        let status = AVCaptureDevice.authorizationStatus(for: .video)
        if status == .notDetermined {
            authorized = await AVCaptureDevice.requestAccess(for: .video)
        } else {
            authorized = status == .authorized
        }
        guard authorized else {
            lastError = "Camera access is required to capture a preservation session."
            return
        }
        session.beginConfiguration()
        session.sessionPreset = .photo
        guard let camera = AVCaptureDevice.default(.builtInWideAngleCamera, for: .video, position: .back),
              let input = try? AVCaptureDeviceInput(device: camera) else {
            lastError = "No wide camera available."
            session.commitConfiguration()
            return
        }
        device = camera
        if session.canAddInput(input) { session.addInput(input) }
        if session.canAddOutput(photo) { session.addOutput(photo) }
        photo.isHighResolutionCaptureEnabled = true
        session.commitConfiguration()
        session.startRunning()
        try? camera.lockForConfiguration()
        camera.videoZoomFactor = 1
        camera.unlockForConfiguration()
    }

    func lockExposureAndWhiteBalance() {
        guard let device else { return }
        do {
            try device.lockForConfiguration()
            let point = CGPoint(x: 0.5, y: 0.5)
            if device.isFocusModeSupported(.locked) {
                device.focusPointOfInterest = point
                device.focusMode = .autoFocus
            }
            if device.isExposureModeSupported(.autoExpose) {
                device.exposurePointOfInterest = point
                device.exposureMode = .autoExpose
            }
            if device.isWhiteBalanceModeSupported(.autoWhiteBalance) {
                device.whiteBalanceMode = .autoWhiteBalance
            }
            device.videoZoomFactor = 1
            device.unlockForConfiguration()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) { [weak self] in
                self?.freezeLock()
            }
        } catch {
            lastError = error.localizedDescription
        }
    }

    private func freezeLock() {
        guard let device else { return }
        do {
            try device.lockForConfiguration()
            if device.isFocusModeSupported(.locked) { device.focusMode = .locked }
            if device.isExposureModeSupported(.locked) { device.exposureMode = .locked }
            if device.isWhiteBalanceModeSupported(.locked) { device.whiteBalanceMode = .locked }
            device.videoZoomFactor = 1
            device.unlockForConfiguration()
            locked = true
        } catch {
            lastError = error.localizedDescription
        }
    }

    func captureJPEG() async throws -> Data {
        try await withCheckedThrowingContinuation { continuation in
            self.continuation = continuation
            let settings = AVCapturePhotoSettings()
            settings.flashMode = .off
            photo.capturePhoto(with: settings, delegate: self)
        }
    }
}

extension LockedCamera: AVCapturePhotoCaptureDelegate {
    func photoOutput(_ output: AVCapturePhotoOutput, didFinishProcessingPhoto photo: AVCapturePhoto, error: Error?) {
        if let error {
            continuation?.resume(throwing: error)
            continuation = nil
            return
        }
        guard let data = photo.fileDataRepresentation() else {
            continuation?.resume(throwing: CameraError.noData)
            continuation = nil
            return
        }
        continuation?.resume(returning: data)
        continuation = nil
    }
}

enum CameraError: Error { case noData }
