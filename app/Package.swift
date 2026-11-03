// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "RDTeamAgent",
    platforms: [.macOS(.v14)],
    targets: [
        // Pure logic: login, Runtime client, attachments, local files, Markdown. No UI.
        .target(name: "RDTeamAgentCore"),
        .executableTarget(name: "RDTeamAgentMac", dependencies: ["RDTeamAgentCore"]),
        .testTarget(name: "RDTeamAgentCoreTests", dependencies: ["RDTeamAgentCore"]),
    ],
    swiftLanguageModes: [.v5]
)
