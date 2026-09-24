// meetseen-app — mevcut web arayüzünü saran macOS uygulaması (WKWebView).
//
// Görevi: (1) webui.py sunucusu çalışmıyorsa başlatmak, (2) arayüzü pencerede
// göstermek, (3) Finder'dan bırakılan/açılan videoyu arayüze ?file= ile iletmek,
// (4) çıkışta KENDİ başlattığı sunucuyu kapatmak.
import SwiftUI
import WebKit
import AppKit

// MARK: - Sunucu yönetimi

final class ServerManager: ObservableObject {
    enum State: Equatable {
        case starting
        case running(URL)
        case failed(String)
    }

    static let shared = ServerManager()
    @Published var state: State = .starting
    @Published var pendingFile: URL?

    private var process: Process?
    private var owned = false
    private var pollTimer: Timer?

    /// Token ~/.meetseen.json'dan okunduğu için GUI ortamında yalnız PATH gerekli.
    private var serverEnv: [String: String] {
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        env["PYTHONUTF8"] = "1"
        return env
    }

    private var urlFile: URL {
        // webui.py /tmp'ye yazar; GUI süreçlerinde NSTemporaryDirectory() farklı yer!
        URL(fileURLWithPath: "/tmp/meetseen-web.url")
    }

    func start() {
        state = .starting
        checkRunning { [weak self] alive in
            guard let self else { return }
            if alive, let url = self.readURLFile() {
                self.state = .running(url)
                return
            }
            self.spawnServer()
        }
    }

    private func readURLFile() -> URL? {
        guard let s = try? String(contentsOf: urlFile, encoding: .utf8),
              let u = URL(string: s.trimmingCharacters(in: .whitespacesAndNewlines)) else {
            return nil
        }
        return u
    }

    private func checkRunning(_ done: @escaping (Bool) -> Void) {
        guard let url = readURLFile() else { return done(false) }
        var req = URLRequest(url: url.appendingPathComponent("api/config"))
        req.timeoutInterval = 1.5
        URLSession.shared.dataTask(with: req) { _, resp, _ in
            done((resp as? HTTPURLResponse)?.statusCode == 200)
        }.resume()
    }

    private func spawnServer() {
        guard let root = ProjectRoot.resolve() else {
            state = .failed("meetseen klasörü seçilemedi")
            return
        }
        let py = root.appendingPathComponent(".venv/bin/python")
        guard FileManager.default.isExecutableFile(atPath: py.path) else {
            state = .failed("Klasörde .venv yok — önce Terminal'de ./install.sh çalıştır:\n\(root.path)")
            return
        }
        let p = Process()
        p.executableURL = py
        p.arguments = [root.appendingPathComponent("webui.py").path]
        // cwd Desktop'ta OLMASIN: Python açılıştaki getcwd'de TCC izni bekleyip
        // bloklanıyor. webui.py BASE'i __file__'dan çözer — cwd gerekmez.
        p.currentDirectoryURL = URL(fileURLWithPath: "/tmp")
        p.environment = serverEnv
        if let log = FileHandle(forWritingAtPath: "/tmp/meetseen-web.log") {
            p.standardOutput = log
            p.standardError = log
        }
        do {
            try p.run()
        } catch {
            state = .failed("Sunucu başlatılamadı: \(error.localizedDescription)")
            return
        }
        process = p
        owned = true
        pollUntilAlive(attempts: 60)
    }

    private func pollUntilAlive(attempts: Int) {
        pollTimer?.invalidate()
        var left = attempts
        pollTimer = Timer.scheduledTimer(withTimeInterval: 0.5, repeats: true) { [weak self] t in
            guard let self else { return t.invalidate() }
            self.checkRunning { alive in
                if alive, let url = self.readURLFile() {
                    t.invalidate()
                    self.state = .running(url)
                } else if self.process?.isRunning == false {
                    t.invalidate()
                    self.state = .failed("Sunucu beklenmedik kapandı — /tmp/meetseen-web.log")
                } else {
                    left -= 1
                    if left <= 0 {
                        t.invalidate()
                        self.state = .failed("Sunucu zamanında yanıt vermedi (30 sn)")
                    }
                }
            }
        }
    }

    /// Yalnız uygulamanın kendi başlattığı sunucuyu kapatır.
    func shutdownIfOwned() {
        pollTimer?.invalidate()
        guard owned, let p = process, p.isRunning else { return }
        p.terminate()
        let deadline = Date().addingTimeInterval(3)
        while p.isRunning && Date() < deadline {
            usleep(150_000)
        }
        if p.isRunning {
            kill(p.processIdentifier, SIGKILL)
        }
    }
}

// MARK: - Proje kökü (webui.py + .venv'nin bulunduğu klasör)

enum ProjectRoot {
    static let defaultsKey = "meetseenProjectDir"

    static func resolve() -> URL? {
        if let s = UserDefaults.standard.string(forKey: defaultsKey) {
            let u = URL(fileURLWithPath: s)
            if isProjectRoot(u) { return u }
        }
        // .build/release içinden çalışıyorsa yukarı doğru otomatik bul
        var dir = URL(fileURLWithPath: CommandLine.arguments[0]).deletingLastPathComponent()
        for _ in 0..<6 {
            if isProjectRoot(dir) {
                UserDefaults.standard.set(dir.path, forKey: defaultsKey)
                return dir
            }
            dir = dir.deletingLastPathComponent()
        }
        return askUser()
    }

    static func isProjectRoot(_ u: URL) -> Bool {
        FileManager.default.fileExists(atPath: u.appendingPathComponent("webui.py").path)
            && FileManager.default.fileExists(atPath: u.appendingPathComponent(".venv/bin/python").path)
    }

    static func askUser() -> URL? {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = false
        panel.message = "meetseen kurulu klasörü seç (içinde webui.py ve .venv olan)"
        guard panel.runModal() == .OK, let u = panel.url, isProjectRoot(u) else { return nil }
        UserDefaults.standard.set(u.path, forKey: defaultsKey)
        return u
    }

    static func reselect() {
        UserDefaults.standard.removeObject(forKey: defaultsKey)
        ServerManager.shared.start()
    }
}

// MARK: - WebView

struct WebPane: NSViewRepresentable {
    let url: URL

    func makeNSView(context: Context) -> WKWebView {
        let wv = WKWebView(frame: .zero, configuration: WKWebViewConfiguration())
        context.coordinator.attach(wv)
        wv.load(URLRequest(url: url))
        return wv
    }

    func updateNSView(_ wv: WKWebView, context: Context) {
        context.coordinator.navigateIfNeeded(to: url, wv: wv)
    }

    func makeCoordinator() -> Nav {
        Nav()
    }

    /// URL değişince (ör. yeni dosya bırakıldı) webview'i yönlendirir.
    final class Nav {
        private var current: URL?
        func attach(_ w: WKWebView) {}
        func navigateIfNeeded(to url: URL, wv: WKWebView) {
            guard url != current else { return }
            current = url
            wv.load(URLRequest(url: url))
        }
    }
}

// MARK: - Arayüz

struct ContentView: View {
    @ObservedObject var server = ServerManager.shared

    var body: some View {
        Group {
            switch server.state {
            case .running(let url):
                WebPane(url: withFile(url, server.pendingFile))
            case .starting:
                VStack(spacing: 14) {
                    ProgressView().controlSize(.large)
                    Text("meetseen sunucusu başlatılıyor…").foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            case .failed(let msg):
                VStack(spacing: 14) {
                    Image(systemName: "exclamationmark.triangle")
                        .font(.system(size: 40)).foregroundStyle(.orange)
                    Text(msg).multilineTextAlignment(.center)
                    HStack {
                        Button("Klasörü Yeniden Seç") { ProjectRoot.reselect() }
                        Button("Tekrar Dene") { server.start() }
                    }
                }
                .padding(32)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            }
        }
        .frame(minWidth: 980, minHeight: 720)
        .onOpenURL { url in server.pendingFile = url }
    }

    private func withFile(_ base: URL, _ file: URL?) -> URL {
        guard let file else { return base }
        var comp = URLComponents(url: base, resolvingAgainstBaseURL: false)!
        comp.queryItems = [URLQueryItem(name: "file", value: file.path)]
        return comp.url ?? base
    }
}

@main
struct MeetseenApp: App {
    @ObservedObject private var server = ServerManager.shared

    var body: some Scene {
        WindowGroup("meetseen") {
            ContentView()
                .onAppear { server.start() }
        }
        .commands {
            CommandGroup(after: .appInfo) {
                Button("Klasörü Yeniden Seç…") { ProjectRoot.reselect() }
                    .keyboardShortcut("r", modifiers: .command)
            }
        }
    }

    init() {
        NotificationCenter.default.addObserver(
            forName: NSApplication.willTerminateNotification,
            object: nil, queue: .main
        ) { _ in
            ServerManager.shared.shutdownIfOwned()
        }
    }
}
