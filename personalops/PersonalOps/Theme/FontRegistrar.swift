//
//  FontRegistrar.swift
//  PersonalOps
//
//  Registers the bundled Silkscreen (pixel display) and Courier Prime
//  (typewriter) fonts with CoreText at launch. SwiftPM resources land in a
//  nested `PersonalOps_PersonalOps.bundle` rather than the app's main
//  bundle, so `UIAppFonts`/Info.plist can't reliably find them — runtime
//  registration via CTFontManager works regardless of where the resource
//  bundle ends up.
//

import CoreText
import Foundation

@MainActor
enum FontRegistrar {
    private static let names = [
        "Silkscreen-Regular", "Silkscreen-Bold",
        "CourierPrime-Regular", "CourierPrime-Bold",
    ]

    private static var didRegister = false

    static func registerBundledFontsIfNeeded() {
        guard !didRegister else { return }
        didRegister = true

        for name in names {
            guard let url = Bundle.module.url(forResource: name, withExtension: "ttf") else {
                continue
            }
            var error: Unmanaged<CFError>?
            CTFontManagerRegisterFontsForURL(url as CFURL, .process, &error)
            // Registration failing (e.g. already registered) is non-fatal —
            // Font.custom will just fall back to the system font if the
            // name genuinely can't be found.
        }
    }
}
