import SwiftUI

struct CaptureView: View {
    @EnvironmentObject var store: SessionStore
    @ObservedObject var camera: LockedCamera
    @State private var pose = PoseQuality()
    @State private var chip: PoseChip = .unlocked
    @State private var message: String?
    @State private var busy = false

    var body: some View {
        ZStack {
            CameraPreview(session: camera.session)
                .ignoresSafeArea()
            VStack(spacing: 0) {
                HStack {
                    Text(store.title.isEmpty ? "Room scan" : store.title)
                        .font(.footnote.weight(.semibold))
                    Spacer()
                    Text("\(store.stills.count) of \(store.stillsTarget) stills")
                        .font(.footnote.weight(.bold))
                        .foregroundStyle(VitrineTheme.green)
                }
                .padding(.horizontal, 16)
                .padding(.vertical, 10)
                .background(.black.opacity(0.45))

                Spacer()

                VStack(spacing: 8) {
                    Text(chip.rawValue)
                        .font(.headline)
                        .padding(.horizontal, 16)
                        .padding(.vertical, 8)
                        .background(.black.opacity(0.55), in: Capsule())
                        .foregroundStyle(chip == .good ? VitrineTheme.green : VitrineTheme.amber)
                    Text(store.currentPass.coaching)
                        .font(.caption)
                        .padding(.horizontal, 14)
                        .padding(.vertical, 8)
                        .background(.black.opacity(0.55), in: Capsule())
                    if let message {
                        Text(message)
                            .font(.caption)
                            .foregroundStyle(VitrineTheme.amber)
                    }
                }

                HStack(alignment: .center, spacing: 28) {
                    VStack {
                        Text(store.currentPass.title)
                            .font(.caption2)
                            .foregroundStyle(VitrineTheme.muted)
                        Button("Pass done") {
                            store.markPassComplete(store.currentPass)
                        }
                        .font(.caption.weight(.semibold))
                    }
                    Button {
                        Task { await shutter() }
                    } label: {
                        ZStack {
                            Circle()
                                .stroke(VitrineTheme.orange, lineWidth: 4)
                                .frame(width: 78, height: 78)
                            Circle()
                                .fill(VitrineTheme.orange)
                                .frame(width: 62, height: 62)
                        }
                    }
                    .disabled(busy || !store.canCapture)
                    NavigationLink {
                        ReviewView()
                    } label: {
                        VStack {
                            Image(systemName: "square.stack")
                            Text("Review")
                                .font(.caption)
                        }
                    }
                    .foregroundStyle(VitrineTheme.cream)
                }
                .padding(.bottom, 28)
                .padding(.top, 16)
                .frame(maxWidth: .infinity)
                .background(.black.opacity(0.55))
            }
        }
        .onAppear { pose.start() }
        .onDisappear { pose.stop() }
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .principal) {
                Text("Vitrine Capture")
                    .font(.caption.weight(.bold))
                    .tracking(1.2)
                    .foregroundStyle(VitrineTheme.orange)
            }
        }
    }

    @MainActor
    private func shutter() async {
        busy = true
        defer { busy = false }
        do {
            let data = try await camera.captureJPEG()
            let sharpness = Sharpness.score(data)
            let verdict = pose.evaluate(translation: .zero, sharpness: sharpness, locked: store.locked)
            chip = verdict
            if verdict != .good {
                try store.rejectStill(data: data, reason: verdict.rawValue)
                message = "Held back: \(verdict.rawValue.lowercased()). The frame is in sidecar/rejected, not stills/."
                return
            }
            _ = try store.acceptStill(data: data, sharpness: sharpness)
            pose.accept(translation: .zero)
            message = nil
        } catch {
            message = error.localizedDescription
        }
    }
}
