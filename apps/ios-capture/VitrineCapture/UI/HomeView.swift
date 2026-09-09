import SwiftUI

struct HomeView: View {
    @EnvironmentObject var store: SessionStore

    var body: some View {
        NavigationStack {
            ZStack {
                VitrineTheme.bg.ignoresSafeArea()
                VStack(alignment: .leading, spacing: 28) {
                    HStack(spacing: 12) {
                        ViewfinderMark()
                            .frame(width: 44, height: 44)
                        VStack(alignment: .leading, spacing: 2) {
                            Text("VITRINE CAPTURE")
                                .font(.caption.weight(.bold))
                                .tracking(1.6)
                                .foregroundStyle(VitrineTheme.orange)
                            Text("Scan the space before ingest.")
                                .font(.footnote)
                                .foregroundStyle(VitrineTheme.muted)
                        }
                    }
                    Text("A reconstruction can only be as complete as the photographs you take in the room.")
                        .font(.title2.weight(.medium))
                        .foregroundStyle(VitrineTheme.cream)
                    Text("Lock the lights. Orbit twice. Close the loop. Then a slow video walk. Export a session the workstation can ingest without guessing.")
                        .foregroundStyle(VitrineTheme.muted)
                    Spacer()
                    NavigationLink {
                        SetupView()
                    } label: {
                        Label("New room scan", systemImage: "camera.viewfinder")
                            .frame(maxWidth: .infinity)
                            .padding(.vertical, 16)
                    }
                    .buttonStyle(.borderedProminent)
                    .tint(VitrineTheme.orange)
                }
                .padding(28)
            }
            .navigationBarTitleDisplayMode(.inline)
        }
    }
}

struct ViewfinderMark: View {
    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 8)
                .stroke(VitrineTheme.cream.opacity(0.85), lineWidth: 2.4)
            Circle()
                .stroke(VitrineTheme.orange, lineWidth: 1.6)
                .padding(11)
            Circle()
                .fill(VitrineTheme.orange)
                .frame(width: 6, height: 6)
        }
    }
}
