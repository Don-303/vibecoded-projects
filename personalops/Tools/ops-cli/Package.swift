// swift-tools-version: 5.9
//
// ops-cli — headless terminal bridge into the PersonalOps SwiftData store.
// macOS 14+ (needs SwiftData). The Sources/OpsCLI/Models directory contains
// SYMLINKS to the app's model files so the CLI and the app always compile
// the exact same schema — do not copy the files.
//
import PackageDescription

let package = Package(
    name: "ops-cli",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "ops-cli",
            path: "Sources/OpsCLI"
        )
    ]
)
