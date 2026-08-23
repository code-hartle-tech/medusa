// swift-tools-version: 6.0
import PackageDescription

// MedusaKit is the transport-independent half of the iOS companion: the
// Companion Protocol codec and the client state machine. It is a SwiftPM
// package rather than an Xcode target so it builds and tests on any machine
// with a Swift toolchain, without Xcode and without an iOS simulator.
//
// The CoreBluetooth and URLSessionWebSocketTask transports live in the app
// target, which does need Xcode. Keeping the protocol here means the part that
// must agree byte-for-byte with the firmware is the part that is testable
// everywhere.
let package = Package(
    name: "MedusaKit",
    platforms: [.macOS(.v13), .iOS(.v16)],
    products: [
        .library(name: "MedusaKit", targets: ["MedusaKit"])
    ],
    targets: [
        .target(name: "MedusaKit"),
        .testTarget(
            name: "MedusaKitTests",
            dependencies: ["MedusaKit"],
            // The cross-language conformance vectors, shared verbatim with the
            // Python, TypeScript, and firmware codecs.
            resources: [.copy("frames.json")]
        ),
    ]
)
