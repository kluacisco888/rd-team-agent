import RDTeamAgentCore
import SwiftUI

@main
struct RDTeamAgentApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        Window("研发团队 Agent", id: "main") {
            RootView()
                .environmentObject(model)
                .frame(minWidth: 1000, minHeight: 640)
        }
        .windowResizability(.contentMinSize)

        WindowGroup("PRD", id: "prd", for: String.self) { $taskId in
            PRDWindow(taskId: taskId)
                .environmentObject(model)
        }

        Settings {
            SettingsView()
                .environmentObject(model)
        }
    }
}
