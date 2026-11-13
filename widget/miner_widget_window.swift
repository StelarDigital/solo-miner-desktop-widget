import Cocoa
import WebKit

final class DragWindow: NSWindow {
    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
}

final class DragWeb: WKWebView {
    var lastDown: NSEvent?
    override func mouseDown(with event: NSEvent) {
        lastDown = event
        super.mouseDown(with: event)
    }
}

final class App: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKScriptMessageHandler {
    var dragMouse = NSPoint.zero
    var dragOrigin = NSPoint.zero
    let frameName = "SoloMinerWidgetWindow"
    var window: DragWindow!
    var web: DragWeb!
    var nativeDrag = false
    var url: URL!

    func applicationDidFinishLaunching(_ n: Notification) {
        let a = CommandLine.arguments
        guard a.count >= 5, let u = URL(string: a[1]), let w = Double(a[2]), let h = Double(a[3]), let r = Double(a[4]) else {
            FileHandle.standardError.write("usage: miner_widget_window url width height cornerRadius\n".data(using: .utf8)!)
            exit(2)
        }
        url = u
        let rect = NSRect(x: 0, y: 0, width: w, height: h)
        window = DragWindow(contentRect: rect, styleMask: [.borderless], backing: .buffered, defer: false)
        window.isOpaque = false
        window.backgroundColor = .clear
        window.hasShadow = true
        window.level = .floating
        window.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle]
        window.isMovableByWindowBackground = true
        window.isReleasedWhenClosed = false

        let cfg = WKWebViewConfiguration()
        cfg.userContentController.add(self, name: "drag")
        web = DragWeb(frame: rect, configuration: cfg)
        web.setValue(false, forKey: "drawsBackground")
        web.navigationDelegate = self
        web.wantsLayer = true
        web.layer?.cornerRadius = CGFloat(r)
        web.layer?.masksToBounds = true
        window.contentView = web

        let name = "SoloMinerWidgetWindow"
        if !window.setFrameUsingName(name) {
            let vf = (NSScreen.main ?? NSScreen.screens[0]).visibleFrame
            window.setFrameOrigin(NSPoint(x: vf.maxX - w - 270, y: vf.maxY - h))
        }
        window.setFrameAutosaveName(name)
        window.orderFrontRegardless()
        load()
    }

    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage) {
        guard m.name == "drag", let b = m.body as? [String: Any], let t = b["type"] as? String else { return }
        switch t {
        case "dragstart":
            dragMouse = NSEvent.mouseLocation
            dragOrigin = window.frame.origin
            nativeDrag = false
            var ev = NSApp.currentEvent
            if ev?.type != .leftMouseDown { ev = web.lastDown }
            if let e = ev, e.type == .leftMouseDown, NSEvent.pressedMouseButtons & 1 != 0,
               abs(ProcessInfo.processInfo.systemUptime - e.timestamp) < 2 {
                nativeDrag = true
                window.performDrag(with: e)
                window.saveFrame(usingName: frameName)
            }
        case "dragmove":
            if nativeDrag { return }
            let p = NSEvent.mouseLocation
            window.setFrameOrigin(NSPoint(x: dragOrigin.x + p.x - dragMouse.x, y: dragOrigin.y + p.y - dragMouse.y))
        case "dragend":
            window.saveFrame(usingName: frameName)
        default: break
        }
    }

    func load() { web.load(URLRequest(url: url, cachePolicy: .reloadIgnoringLocalCacheData)) }

    func retry() { DispatchQueue.main.asyncAfter(deadline: .now() + 5) { self.load() } }
    func webView(_ w: WKWebView, didFailProvisionalNavigation n: WKNavigation!, withError e: Error) { retry() }
    func webView(_ w: WKWebView, didFail n: WKNavigation!, withError e: Error) { retry() }
    func webViewWebContentProcessDidTerminate(_ w: WKWebView) { retry() }
}

let app = NSApplication.shared
app.setActivationPolicy(.accessory)
let delegate = App()
app.delegate = delegate
app.run()
