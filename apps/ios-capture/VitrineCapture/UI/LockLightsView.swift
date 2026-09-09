import AVFoundation
import SwiftUI

struct LockLightsView: View {
    @EnvironmentObject var store: SessionStore
    @StateObject private var camera = LockedCamera()

    var body: some View {
        ZStack {
            CameraPreview(session: camera.session)
                .ignoresSafeArea()
            VStack {
                Spacer()
                VStack(alignment: .leading, spacing: 12) {
                    Text("Point at mid-room depth and lock.")
                        .font(.headline)
                        .foregroundStyle(VitrineTheme.cream)
                    Text("Auto-exposure and white balance must not drift between stills. The optimiser otherwise explains the same wall as two colours.")
                        .font(.footnote)
                        .foregroundStyle(VitrineTheme.muted)
                    Button(camera.locked ? "Lights locked" : "Lock AE · AWB · AF") {
                        camera.lockExposureAndWhiteBalance()
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(camera.locked ? VitrineTheme.green : VitrineTheme.orange)
                    .disabled(!camera.authorized)
                    NavigationLink("Start capture") {
                        CaptureView(camera: camera)
                    }
                    .disabled(!camera.locked)
                    .foregroundStyle(camera.locked ? VitrineTheme.cream : VitrineTheme.muted)
                }
                .padding(20)
                .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 18))
                .padding()
            }
        }
        .task { await camera.start() }
        .onChange(of: camera.locked) { _, locked in
            store.locked = locked
        }
        .navigationTitle("Lock lights")
        .navigationBarTitleDisplayMode(.inline)
    }
}

struct CameraPreview: UIViewRepresentable {
    let session: AVCaptureSession

    func makeUIView(context: Context) -> PreviewView {
        let view = PreviewView()
        view.previewLayer.session = session
        view.previewLayer.videoGravity = .resizeAspectFill
        return view
    }

    func updateUIView(_ uiView: PreviewView, context: Context) {
        uiView.previewLayer.session = session
    }
}

final class PreviewView: UIView {
    override class var layerClass: AnyClass { AVCaptureVideoPreviewLayer.self }
    var previewLayer: AVCaptureVideoPreviewLayer { layer as! AVCaptureVideoPreviewLayer }
}
