import RDTeamAgentCore
import SwiftUI

@main
struct RDTeamAgentApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        // A WindowGroup, not a Window: after quitting with the window open, a
        // relaunched `Window` scene came back with no window at all. The New
        // Window command is removed so there is still one main window.
        WindowGroup("研发团队 Agent", id: "main") {
            RootView()
                .environmentObject(model)
                .frame(minWidth: 1000, minHeight: 640)
        }
        .windowResizability(.contentMinSize)
        .commands {
            CommandGroup(replacing: .newItem) {}
        }

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
