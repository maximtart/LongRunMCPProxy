/// xcode-auto-approve — Auto-approve Xcode MCP agent permission dialogs.
///
/// Uses AXUIElement API (Accessibility framework) to find and click the "Allow"
/// button on Xcode's MCP connection dialog. Works cross-Space because AXUIElement
/// talks directly to the app process via Mach IPC, not through WindowServer.
///
/// Based on XcodeMCPKit's XcodePermissionDialogAutoApprover.swift.
/// Source: https://github.com/lynnswap/XcodeMCPKit
///
/// Usage:  xcode-auto-approve [--agent-name NAME] [--interval MS] [--verbose]
/// Requires: Accessibility permission for the calling process.

import AppKit
import ApplicationServices
import Foundation

// MARK: - Configuration

struct Config {
    var agentName: String = "longrun-mcp-proxy"
    var pollIntervalMs: UInt32 = 250
    var verbose: Bool = false
}

func parseArgs() -> Config {
    var config = Config()
    var args = CommandLine.arguments.dropFirst()
    while let arg = args.popFirst() {
        switch arg {
        case "--agent-name":
            if let val = args.popFirst() { config.agentName = val }
        case "--interval":
            if let val = args.popFirst(), let ms = UInt32(val) { config.pollIntervalMs = ms }
        case "--verbose":
            config.verbose = true
        case "--help", "-h":
            print("Usage: xcode-auto-approve [--agent-name NAME] [--interval MS] [--verbose]")
            print("  --agent-name  Name to match in dialog text (default: longrun-mcp-proxy)")
            print("  --interval    Poll interval in milliseconds (default: 250)")
            print("  --verbose     Print all scanned windows")
            exit(0)
        default:
            break
        }
    }
    return config
}

// MARK: - Logging

func log(_ level: String, _ message: String) {
    let ts = ISO8601DateFormatter().string(from: Date())
    print("[\(ts)] [\(level)] \(message)")
    fflush(stdout)
}

// MARK: - AXUIElement Helpers

func axCopyString(_ element: AXUIElement, _ attribute: String) -> String? {
    var value: CFTypeRef?
    let err = AXUIElementCopyAttributeValue(element, attribute as CFString, &value)
    guard err == .success else { return nil }
    return value as? String
}

func axCopyBool(_ element: AXUIElement, _ attribute: String) -> Bool? {
    var value: CFTypeRef?
    let err = AXUIElementCopyAttributeValue(element, attribute as CFString, &value)
    guard err == .success else { return nil }
    return value as? Bool
}

func axCopyElement(_ element: AXUIElement, _ attribute: String) -> AXUIElement? {
    var value: CFTypeRef?
    let err = AXUIElementCopyAttributeValue(element, attribute as CFString, &value)
    guard err == .success else { return nil }
    guard CFGetTypeID(value!) == AXUIElementGetTypeID() else { return nil }
    return (value as! AXUIElement)
}

func axCopyElementArray(_ element: AXUIElement, _ attribute: String) -> [AXUIElement] {
    var value: CFTypeRef?
    let err = AXUIElementCopyAttributeValue(element, attribute as CFString, &value)
    guard err == .success else { return [] }
    if let arr = value as? [AXUIElement] { return arr }
    if let arr = value as? [AnyObject] {
        return arr.compactMap { obj in
            guard CFGetTypeID(obj) == AXUIElementGetTypeID() else { return nil }
            return (obj as! AXUIElement)
        }
    }
    return []
}

// MARK: - Text Collection (BFS traversal of AX tree)

func collectTextValues(from root: AXUIElement, maxDescendants: Int = 128) -> [String] {
    var queue: [AXUIElement] = [root]
    var values: [String] = []
    var visited = 0

    while !queue.isEmpty && visited < maxDescendants {
        let element = queue.removeFirst()
        visited += 1

        if let title = axCopyString(element, kAXTitleAttribute as String) {
            values.append(title)
        }
        if let value = axCopyString(element, kAXValueAttribute as String) {
            values.append(value)
        }
        if let desc = axCopyString(element, kAXDescriptionAttribute as String) {
            values.append(desc)
        }

        let children = axCopyElementArray(element, kAXChildrenAttribute as String)
        queue.append(contentsOf: children)
    }

    // Deduplicate, trim empty
    var seen = Set<String>()
    return values.compactMap { val in
        let trimmed = val.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, seen.insert(trimmed).inserted else { return nil }
        return trimmed
    }
}

// MARK: - Dialog Detection

struct DialogMatch {
    let processID: pid_t
    let defaultButton: AXUIElement
    let fingerprint: String
    let buttonTitle: String
}

func isXcodeMCPDialog(
    window: AXUIElement,
    processID: pid_t,
    agentName: String,
    serverPIDs: Set<pid_t>
) -> DialogMatch? {
    // Structural checks
    let subrole = axCopyString(window, kAXSubroleAttribute as String) ?? ""
    guard subrole == "AXDialog" || subrole == "AXSystemDialog" else { return nil }

    let isModal = axCopyBool(window, kAXModalAttribute as String) ?? false
    guard isModal else { return nil }

    guard let defaultButton = axCopyElement(window, kAXDefaultButtonAttribute as String) else { return nil }

    // Skip normal workspace windows (have document or proxy + isMain)
    let isMain = axCopyBool(window, kAXMainAttribute as String) ?? false
    let hasDocument = axCopyString(window, kAXDocumentAttribute as String) != nil
    let hasProxy = axCopyElement(window, kAXProxyAttribute as String) != nil
    if isMain && (hasDocument || hasProxy) { return nil }

    let isMinimized = axCopyBool(window, kAXMinimizedAttribute as String) ?? false
    if isMinimized { return nil }

    // Collect text from dialog
    let texts = collectTextValues(from: window)
    let allText = texts.joined(separator: " ").lowercased()

    // Must contain agent name or PID
    let agentLower = agentName.lowercased()
    let containsAgent = allText.contains(agentLower)
    let containsPID = serverPIDs.contains { pid in
        allText.contains("\(pid)")
    }
    guard containsAgent || containsPID else { return nil }

    // Build fingerprint for dedup
    let buttonTitle = axCopyString(defaultButton, kAXTitleAttribute as String)
        ?? axCopyString(defaultButton, kAXDescriptionAttribute as String)
        ?? "unknown"
    let fingerprint = "\(processID)|\(subrole)|\(isModal)|\(texts.joined(separator: "|"))"

    return DialogMatch(
        processID: processID,
        defaultButton: defaultButton,
        fingerprint: fingerprint,
        buttonTitle: buttonTitle
    )
}

// MARK: - Xcode Process Discovery

func runningXcodeProcessIDs() -> [pid_t] {
    let bundleIDs: Set<String> = [
        "com.apple.dt.Xcode",
        "com.apple.dt.ExternalViewService",
    ]
    return NSWorkspace.shared.runningApplications.compactMap { app in
        guard let bid = app.bundleIdentifier, bundleIDs.contains(bid), !app.isTerminated else {
            return nil
        }
        return app.processIdentifier
    }
}

// MARK: - Server PID Candidates

func serverProcessIDCandidates() -> Set<pid_t> {
    var candidates: Set<pid_t> = [ProcessInfo.processInfo.processIdentifier]
    // Also include parent PID
    candidates.insert(getppid())
    // Include children of current process
    // (mcpbridge is a child of the proxy which is a child of us)
    let myPID = ProcessInfo.processInfo.processIdentifier
    let pipe = Pipe()
    let task = Process()
    task.executableURL = URL(fileURLWithPath: "/bin/ps")
    task.arguments = ["-o", "pid=", "-g", "\(myPID)"]
    task.standardOutput = pipe
    task.standardError = FileHandle.nullDevice
    try? task.run()
    task.waitUntilExit()
    let data = pipe.fileHandleForReading.readDataToEndOfFile()
    if let output = String(data: data, encoding: .utf8) {
        for line in output.split(separator: "\n") {
            if let pid = pid_t(line.trimmingCharacters(in: .whitespaces)) {
                candidates.insert(pid)
            }
        }
    }
    return candidates
}

// MARK: - Main Loop

func main() {
    let config = parseArgs()

    // Check Accessibility permission
    if !AXIsProcessTrusted() {
        log("WARN", "Accessibility permission not granted. Requesting...")
        let options: NSDictionary = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as NSString: true]
        _ = AXIsProcessTrustedWithOptions(options)
        log("WARN", "Waiting for Accessibility permission...")
        while !AXIsProcessTrusted() {
            usleep(1_000_000) // 1s
        }
        log("INFO", "Accessibility permission granted.")
    }

    log("INFO", "Monitoring Xcode for MCP permission dialogs (agent=\(config.agentName), interval=\(config.pollIntervalMs)ms)")

    var clickedFingerprints = Set<String>()
    let serverPIDs = serverProcessIDCandidates()
    log("INFO", "Server PID candidates: \(serverPIDs.sorted())")

    // Use Timer + RunLoop instead of usleep — NSWorkspace notifications
    // require an active RunLoop to update runningApplications.
    Timer.scheduledTimer(withTimeInterval: Double(config.pollIntervalMs) / 1000.0, repeats: true) { _ in
        let xcodePIDs = runningXcodeProcessIDs()

        for pid in xcodePIDs {
            let app = AXUIElementCreateApplication(pid)
            let windows = axCopyElementArray(app, kAXWindowsAttribute as String)

            if config.verbose && !windows.isEmpty {
                log("DEBUG", "Xcode PID \(pid): \(windows.count) windows")
            }

            for window in windows {
                if let match = isXcodeMCPDialog(
                    window: window,
                    processID: pid,
                    agentName: config.agentName,
                    serverPIDs: serverPIDs
                ) {
                    if clickedFingerprints.contains(match.fingerprint) {
                        continue
                    }

                    let err = AXUIElementPerformAction(match.defaultButton, kAXPressAction as CFString)
                    if err == .success {
                        log("INFO", "Auto-approved dialog (pid=\(pid), button=\(match.buttonTitle))")
                        clickedFingerprints.insert(match.fingerprint)
                    } else {
                        log("ERROR", "Failed to press button (pid=\(pid), error=\(err.rawValue))")
                    }
                }
            }
        }
    }

    // Run the main RunLoop — required for Timer and NSWorkspace updates
    RunLoop.main.run()
}

main()
