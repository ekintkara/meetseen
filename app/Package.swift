// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "meetseen-app",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(
            name: "meetseen-app",
            path: "Sources/App"
        )
    ]
)
