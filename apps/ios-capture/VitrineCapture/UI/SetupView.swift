import SwiftUI

struct SetupView: View {
    @EnvironmentObject var store: SessionStore

    var body: some View {
        Form {
            Section("What is being preserved") {
                TextField("Title", text: $store.title)
                TextField("Venue (optional)", text: $store.venue)
                TextField("Subject", text: $store.subject, axis: .vertical)
                    .lineLimit(3...6)
            }
            Section("Room size") {
                Picker("Room size", selection: $store.roomSize) {
                    ForEach(RoomSize.allCases) { size in
                        Text(size.label).tag(size)
                    }
                }
                Text("Suggested stills: \(store.stillsTarget). Coverage and the checklist gate export, not the counter.")
                    .font(.footnote)
                    .foregroundStyle(VitrineTheme.muted)
            }
            Section("Screens") {
                Text("A playing screen is both view-dependent and time-varying. There is no neutral option.")
                    .font(.footnote)
                    .foregroundStyle(VitrineTheme.muted)
                Picker("Screens", selection: $store.screensPolicy) {
                    Text("Choose…").tag(Optional<ScreensPolicy>.none)
                    ForEach(ScreensPolicy.allCases) { policy in
                        Text(policy.label).tag(Optional(policy))
                    }
                }
            }
            Section("Mirrors") {
                Picker("Mirrors", selection: $store.mirrorsPolicy) {
                    Text("Choose…").tag(Optional<MirrorsPolicy>.none)
                    ForEach(MirrorsPolicy.allCases) { policy in
                        Text(policy.label).tag(Optional(policy))
                    }
                }
            }
            Section {
                NavigationLink("Lock lights") {
                    LockLightsView()
                }
                .disabled(!store.setupComplete)
            }
        }
        .scrollContentBackground(.hidden)
        .background(VitrineTheme.bg)
        .navigationTitle("Session setup")
        .navigationBarTitleDisplayMode(.inline)
    }
}
