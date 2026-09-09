import SwiftUI

struct ReviewView: View {
    @EnvironmentObject var store: SessionStore
    @State private var exportNote: String?
    @State private var exportURL: URL?

    var body: some View {
        List {
            Section("Coverage") {
                LabeledContent("Stills", value: "\(store.stills.count) / \(store.stillsTarget)")
                Toggle("Two heights", isOn: $store.twoHeights)
                Toggle("Loop closed", isOn: $store.loopClosed)
                Toggle("Detail pass", isOn: $store.detailPassDone)
                Toggle("Corners covered", isOn: $store.cornersNoted)
                Toggle("Floor edges covered", isOn: $store.floorEdgesNoted)
            }
            Section("Accepted stills") {
                if store.stills.isEmpty {
                    Text("No stills yet.")
                        .foregroundStyle(VitrineTheme.muted)
                } else {
                    ForEach(store.stills) { still in
                        HStack {
                            Text(still.url.lastPathComponent)
                            Spacer()
                            Text(still.pass.title)
                                .font(.caption)
                                .foregroundStyle(VitrineTheme.muted)
                        }
                    }
                }
            }
            Section("Rejected on device") {
                Text("\(store.rejected.count) frames held in sidecar/rejected. Ingest never sees them.")
                    .font(.footnote)
                    .foregroundStyle(VitrineTheme.muted)
            }
            Section {
                Button("Write capture.json") {
                    do {
                        _ = try SessionExporter.writeJSON(from: store)
                        exportNote = "Session written to Documents. Copy the folder or zip it, then AirDrop to the workstation."
                        exportURL = store.root
                    } catch {
                        exportNote = error.localizedDescription
                    }
                }
                .tint(VitrineTheme.orange)
                if let exportNote {
                    Text(exportNote)
                        .font(.footnote)
                        .foregroundStyle(VitrineTheme.muted)
                }
                if let exportURL {
                    ShareLink(item: exportURL) {
                        Label("Share session folder", systemImage: "square.and.arrow.up")
                    }
                }
            } footer: {
                Text("Unlocked AE/AWB, missing stills, or a hash failure block export. An incomplete checklist only warns.")
            }
        }
        .scrollContentBackground(.hidden)
        .background(VitrineTheme.bg)
        .navigationTitle("Review")
    }
}
