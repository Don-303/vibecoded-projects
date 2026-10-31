//
//  NousTheme.swift
//  PersonalOps
//
//  Solarized Dark palette (muted deep teal + cyan-blue accent). Two font
//  identities: `Silkscreen` (pixel) for main section headers/titles and
//  `Courier Prime`/system monospace for body/UI text.
//
//  Palette contract:
//    background   #002B36  deep teal screen background
//    panel        #073642  slightly lighter teal — cards, list rows, input fields
//    panelAlt     #268BD2  cyan-blue — alternate/lifted panel accents, also
//                 the primary interactive accent color
//    onBackground #93A1A1  muted gray-cyan text/icon color, used on every
//                 surface
//    statusActive #39FF14  neon green — completed/active flags only
//    statusPending#FF9F0A  amber — pending states / empty-state code lines
//    alert        #FF453A  destructive actions
//
//  Aesthetic rules: sharp 90-degree corners only (cornerRadius forced to 0),
//  no drop shadows (consoleGlow is an intentional no-op), no gradients.
//

import SwiftUI

enum NousTheme {
    // MARK: - Palette

    static let background = Color(red: 0x00 / 255, green: 0x2B / 255, blue: 0x36 / 255)
    /// Nav/tab bar chrome — same lighter teal as `panel`, for a single
    /// consistent "block" surface distinct from the background.
    static let backgroundAlt = Color(red: 0x07 / 255, green: 0x36 / 255, blue: 0x42 / 255)

    /// Default fill for `consolePanel` — cards, list rows, input fields.
    static let panel = backgroundAlt
    /// Alternate/lifted panel accent — cyan-blue.
    static let panelAlt = Color(red: 0x26 / 255, green: 0x8B / 255, blue: 0xD2 / 255)

    /// Muted gray-cyan text/icon color, used on every surface (background
    /// and every panel are dark, so one text color covers both).
    static let onBackground = Color(red: 0x93 / 255, green: 0xA1 / 255, blue: 0xA1 / 255)
    static let textPrimary = onBackground
    static let ink = onBackground

    /// Primary interactive/highlight color — cyan-blue.
    static let accent = panelAlt

    static let statusActive = Color(red: 0x39 / 255, green: 0xFF / 255, blue: 0x14 / 255)
    static let statusPending = Color(red: 0xFF / 255, green: 0x9F / 255, blue: 0x0A / 255)
    static let alert = Color(red: 0xFF / 255, green: 0x45 / 255, blue: 0x3A / 255)

    static let dimText = textPrimary.opacity(0.45)

    // MARK: - Metrics

    /// Brutalist rule: no rounded corners, anywhere.
    static let cornerRadius: CGFloat = 0
    static let borderWidth: CGFloat = 1.5

    // MARK: - Typography
    //
    // Three registers: Silkscreen (pixel/bitmap) for headlines, Courier
    // Prime (typewriter) for technical/log/raw-content text, and system
    // monospace (SF Mono, via `.fontDesign(.monospaced)`) everywhere else —
    // labels, chrome, buttons, general utility text.

    /// Blocky pixel display type for main headlines — section labels and
    /// screen titles. `relativeTo:` keeps Dynamic Type scaling working.
    static func pixelFont(size: CGFloat, relativeTo style: Font.TextStyle = .headline) -> Font {
        .custom("Silkscreen-Regular", size: size, relativeTo: style)
    }

    static func pixelFontBold(size: CGFloat, relativeTo style: Font.TextStyle = .headline) -> Font {
        .custom("Silkscreen-Bold", size: size, relativeTo: style)
    }

    /// Typewriter type for technical/raw content — log entries, chat
    /// messages, task/habit detail text.
    static func typewriterFont(size: CGFloat, relativeTo style: Font.TextStyle = .body) -> Font {
        .custom("CourierPrime-Regular", size: size, relativeTo: style)
    }

    static func typewriterFontBold(size: CGFloat, relativeTo style: Font.TextStyle = .body) -> Font {
        .custom("CourierPrime-Bold", size: size, relativeTo: style)
    }
}

// MARK: - Glow (intentionally inert)

extension View {
    /// No-op. The "Neo-Classical Cyberpunk / Utilitarian Brutalist" theme
    /// forbids drop shadows outright; this stays as a call-site-compatible
    /// shim so existing `.consoleGlow(...)` usages don't need to be torn out
    /// one by one.
    func consoleGlow(_ color: Color = NousTheme.accent, radius: CGFloat = 4) -> some View {
        self
    }
}

// MARK: - Panel chrome

private struct ConsolePanel: ViewModifier {
    var borderColor: Color = NousTheme.onBackground
    var borderOpacity: Double = 1
    var fill: Color = NousTheme.panel

    func body(content: Content) -> some View {
        content
            .background(fill)
            .overlay(
                Rectangle()
                    .strokeBorder(borderColor.opacity(borderOpacity), lineWidth: NousTheme.borderWidth)
            )
            .clipShape(Rectangle())
    }
}

extension View {
    func consolePanel(
        border: Color = NousTheme.onBackground,
        opacity: Double = 1,
        fill: Color = NousTheme.panel
    ) -> some View {
        modifier(ConsolePanel(borderColor: border, borderOpacity: opacity, fill: fill))
    }
}

// MARK: - Root background

private struct ConsoleBackground: ViewModifier {
    func body(content: Content) -> some View {
        content
            .background(NousTheme.background.ignoresSafeArea())
            .scrollContentBackground(.hidden)
    }
}

extension View {
    func consoleBackground() -> some View {
        modifier(ConsoleBackground())
    }
}

// MARK: - Button style

/// Sharp-bordered blue block that inverts to a solid white fill on press —
/// the only surface in the theme that briefly becomes white, and only
/// while actively pressed.
struct ShellButtonStyle: ButtonStyle {
    var tint: Color = NousTheme.onBackground

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.system(.subheadline, design: .monospaced).weight(.bold))
            .foregroundStyle(configuration.isPressed ? NousTheme.background : tint)
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .background(configuration.isPressed ? tint : NousTheme.background)
            .overlay(
                Rectangle().stroke(tint, lineWidth: NousTheme.borderWidth)
            )
            .contentShape(Rectangle())
            .animation(.easeOut(duration: 0.08), value: configuration.isPressed)
    }
}

extension ButtonStyle where Self == ShellButtonStyle {
    static func shell(_ tint: Color = NousTheme.onBackground) -> ShellButtonStyle {
        ShellButtonStyle(tint: tint)
    }
}

// MARK: - Section label

/// Wide-spaced, chiseled serif header — the "classical stone inscription"
/// register.
struct ConsoleSectionLabel: View {
    let text: String
    var tint: Color = NousTheme.onBackground

    var body: some View {
        Text(text.uppercased())
            .font(NousTheme.pixelFont(size: 13, relativeTo: .footnote))
            .foregroundStyle(tint)
            .tracking(2.5)
    }
}

// MARK: - Empty state

/// Status line for empty screens, e.g.
/// `[SYSTEM_STATUS // NO_ACTIVE_DATA_PAYLOADS_FOUND]`.
struct ConsoleEmptyState: View {
    let code: String
    var detail: String? = nil

    var body: some View {
        VStack(spacing: 6) {
            Text("[SYSTEM_STATUS // \(code)]")
                .font(.system(.footnote, design: .monospaced).weight(.semibold))
                .foregroundStyle(NousTheme.statusPending)
            if let detail {
                Text(detail)
                    .font(.system(.caption2, design: .monospaced))
                    .foregroundStyle(NousTheme.statusPending.opacity(0.7))
            }
        }
        .multilineTextAlignment(.center)
        .padding(.horizontal, 24)
        .accessibilityElement(children: .combine)
    }
}

// MARK: - Blinking cursor

/// A pulsing block cursor, e.g. trailing a command prompt. White, so it
/// reads against the blue input fields it's typically placed inside.
struct BlinkingCursor: View {
    var color: Color = NousTheme.onBackground
    @State private var visible = true

    var body: some View {
        Rectangle()
            .fill(color)
            .frame(width: 8, height: 16)
            .opacity(visible ? 1 : 0.15)
            .onAppear {
                withAnimation(.easeInOut(duration: 0.6).repeatForever(autoreverses: true)) {
                    visible = false
                }
            }
    }
}
