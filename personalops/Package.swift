// swift-tools-version: 6.0
//
// xtool SwiftPM manifest — lets the app build outside Xcode via
// `xtool dev` / `xtool build` once the Darwin SDK is installed
// (`xtool sdk install <path-to-Xcode.xip>`).
//
// Sources/PersonalOps is a symlink to ./PersonalOps, the same source
// tree the Xcode project uses — one source of truth for both build paths.
//
// Persistence is GRDB (not SwiftData): SwiftData's @Model macro is a
// closed-source macOS-only compiler plugin, so it cannot build on Linux.
// GRDB + GRDBQuery are pure open source and cross-compile cleanly.

import PackageDescription

let package = Package(
    name: "PersonalOps",
    platforms: [
        .iOS(.v17),
        .macOS(.v14),
    ],
    products: [
        // An xtool project should contain exactly one library product,
        // representing the main app.
        .library(
            name: "PersonalOps",
            targets: ["PersonalOps"]
        ),
    ],
    dependencies: [
        .package(url: "https://github.com/groue/GRDB.swift", from: "7.0.0"),
        .package(url: "https://github.com/groue/GRDBQuery", from: "0.11.0"),
    ],
    targets: [
        .target(
            name: "PersonalOps",
            dependencies: [
                .product(name: "GRDB", package: "GRDB.swift"),
                .product(name: "GRDBQuery", package: "GRDBQuery"),
            ],
            resources: [
                .process("Fonts"),
            ]
        ),
    ]
)
