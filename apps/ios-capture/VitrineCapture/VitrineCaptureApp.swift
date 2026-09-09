import SwiftUI

@main
struct VitrineCaptureApp: App {
    @StateObject private var store = SessionStore()

    var body: some Scene {
        WindowGroup {
            HomeView()
                .environmentObject(store)
                .preferredColorScheme(.dark)
        }
    }
}
