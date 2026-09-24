// tools/make_icon.swift — meetseen uygulama ikonu üretir (1024 px PNG).
// Kullanım: swift tools/make_icon.swift cikti.png
import AppKit

let out = CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "icon1024.png"
let S: CGFloat = 1024

let img = NSImage(size: NSSize(width: S, height: S))
img.lockFocus()

// köşeleri yuvarlatılmış zemin + marka gradyanı (web logosuyla aynı: indigo→mor)
let path = NSBezierPath(roundedRect: NSRect(x: 0, y: 0, width: S, height: S),
                        xRadius: S * 0.22, yRadius: S * 0.22)
let grad = NSGradient(starting: NSColor(srgbRed: 0.31, green: 0.27, blue: 0.90, alpha: 1),
                      ending: NSColor(srgbRed: 0.66, green: 0.33, blue: 0.97, alpha: 1))
grad?.draw(in: path, angle: -70)

// kalın beyaz "m"
let attrs: [NSAttributedString.Key: Any] = [
    .font: NSFont.systemFont(ofSize: S * 0.58, weight: .bold),
    .foregroundColor: NSColor.white,
]
let s = NSAttributedString(string: "m", attributes: attrs)
let b = s.boundingRect(with: NSSize(width: S, height: S), options: [.usesLineFragmentOrigin])
s.draw(at: NSPoint(x: (S - b.width) / 2, y: (S - b.height) / 2 - S * 0.02))

img.unlockFocus()

let rep = NSBitmapImageRep(data: img.tiffRepresentation!)!
try! rep.representation(using: .png, properties: [:])!
    .write(to: URL(fileURLWithPath: out))
print("yazıldı: \(out)")
