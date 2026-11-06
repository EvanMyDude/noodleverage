// OCR a PDF whose text is drawn as vector paths, using only built-in macOS frameworks.
//
//   swift tools/align/ocr_pdf.swift <in.pdf> <out.json> [pngDir] [scale]
//
// For every page: render with PDFKit at `scale`x, run Vision's accurate text recognizer,
// and emit line + word boxes in PDF points (top-left origin). Each word also carries a
// `stroke` estimate (mean horizontal run length of dark pixels, in rendered pixels), which
// the Python side uses to tell bold from regular weight. Any real text-layer characters
// (footers, arrow glyphs) are emitted separately so they never depend on OCR.

import AppKit
import Foundation
import PDFKit
import Vision

struct Word: Codable { let text: String; let bbox: [Double]; let stroke: Double }
struct Line: Codable { let text: String; let conf: Double; let bbox: [Double]; let words: [Word] }
struct LayerChar: Codable { let ch: String; let bbox: [Double] }
struct Page: Codable {
    let index: Int; let width: Double; let height: Double; let scale: Double
    let lines: [Line]; let textLayer: [LayerChar]
}
struct Output: Codable { let source: String; let engine: String; let pages: [Page] }

let args = CommandLine.arguments
guard args.count >= 3 else {
    FileHandle.standardError.write("usage: ocr_pdf.swift <in.pdf> <out.json> [pngDir] [scale]\n".data(using: .utf8)!)
    exit(2)
}
let pdfURL = URL(fileURLWithPath: args[1])
let outURL = URL(fileURLWithPath: args[2])
let pngDir: URL? = args.count >= 4 && !args[3].isEmpty ? URL(fileURLWithPath: args[3]) : nil
let scale = args.count >= 5 ? Double(args[4]) ?? 3.0 : 3.0

guard let doc = PDFDocument(url: pdfURL) else {
    FileHandle.standardError.write("cannot open PDF: \(pdfURL.path)\n".data(using: .utf8)!)
    exit(1)
}
if let dir = pngDir { try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true) }

/// Render a page to an 8-bit grayscale bitmap (white background).
func render(_ page: PDFPage, scale: Double) -> (CGImage, [UInt8], Int, Int) {
    let box = page.bounds(for: .mediaBox)
    let w = Int((box.width * scale).rounded()), h = Int((box.height * scale).rounded())
    var pixels = [UInt8](repeating: 255, count: w * h)
    let ctx = CGContext(data: &pixels, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w,
                        space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue)!
    ctx.setFillColor(gray: 1, alpha: 1)
    ctx.fill(CGRect(x: 0, y: 0, width: w, height: h))
    ctx.scaleBy(x: scale, y: scale)
    ctx.translateBy(x: -box.minX, y: -box.minY)
    page.draw(with: .mediaBox, to: ctx)
    let image = ctx.makeImage()!
    return (image, pixels, w, h)
}

/// Mean dark-run length across rows of a pixel rect: a weight-sensitive stroke-width proxy.
func strokeWidth(_ px: [UInt8], _ w: Int, _ h: Int, _ r: CGRect) -> Double {
    let x0 = max(0, Int(r.minX)), x1 = min(w, Int(r.maxX))
    let y0 = max(0, Int(r.minY)), y1 = min(h, Int(r.maxY))
    if x1 <= x0 || y1 <= y0 { return 0 }
    var dark = 0, runs = 0
    for y in y0..<y1 {
        var inRun = false
        let row = y * w
        for x in x0..<x1 {
            let d = px[row + x] < 140
            if d { dark += 1; if !inRun { runs += 1; inRun = true } } else { inRun = false }
        }
    }
    return runs == 0 ? 0 : Double(dark) / Double(runs)
}

var pages: [Page] = []
for i in 0..<doc.pageCount {
    guard let page = doc.page(at: i) else { continue }
    let box = page.bounds(for: .mediaBox)
    let (image, pixels, pw, ph) = render(page, scale: scale)

    if let dir = pngDir {
        let url = dir.appendingPathComponent(String(format: "page-%02d.png", i + 1))
        if let dest = CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil) {
            CGImageDestinationAddImage(dest, image, nil)
            CGImageDestinationFinalize(dest)
        }
    }

    let req = VNRecognizeTextRequest()
    req.recognitionLevel = .accurate
    req.usesLanguageCorrection = true
    req.recognitionLanguages = ["en-US"]
    let handler = VNImageRequestHandler(cgImage: image, options: [:])
    try handler.perform([req])

    // Vision boxes are normalized with a bottom-left origin; convert to PDF points, top-left origin.
    func toPoints(_ r: CGRect) -> [Double] {
        let x = Double(r.minX) * box.width, y = (1 - Double(r.maxY)) * box.height
        return [x, y, Double(r.width) * box.width, Double(r.height) * box.height]
    }
    func toPixels(_ r: CGRect) -> CGRect {
        CGRect(x: r.minX * CGFloat(pw), y: (1 - r.maxY) * CGFloat(ph),
               width: r.width * CGFloat(pw), height: r.height * CGFloat(ph))
    }

    var lines: [Line] = []
    for obs in req.results ?? [] {
        guard let cand = obs.topCandidates(1).first else { continue }
        let s = cand.string
        var words: [Word] = []
        var idx = s.startIndex
        while idx < s.endIndex {
            while idx < s.endIndex && s[idx] == " " { idx = s.index(after: idx) }
            if idx >= s.endIndex { break }
            var end = idx
            while end < s.endIndex && s[end] != " " { end = s.index(after: end) }
            let range = idx..<end
            if let rect = try? cand.boundingBox(for: range)?.boundingBox {
                words.append(Word(text: String(s[range]), bbox: toPoints(rect),
                                  stroke: strokeWidth(pixels, pw, ph, toPixels(rect))))
            } else {
                words.append(Word(text: String(s[range]), bbox: [], stroke: 0))
            }
            idx = end
        }
        lines.append(Line(text: s, conf: Double(cand.confidence), bbox: toPoints(obs.boundingBox), words: words))
    }

    // Real text-layer characters (footer, arrows) with their bounds.
    var layer: [LayerChar] = []
    if let str = page.string {
        let ns = str as NSString
        for k in 0..<ns.length {
            let ch = ns.substring(with: NSRange(location: k, length: 1))
            if ch.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { continue }
            let b = page.characterBounds(at: k)
            layer.append(LayerChar(ch: ch, bbox: [Double(b.minX - box.minX), Double(box.maxY - b.maxY),
                                                  Double(b.width), Double(b.height)]))
        }
    }
    pages.append(Page(index: i + 1, width: Double(box.width), height: Double(box.height), scale: scale,
                      lines: lines, textLayer: layer))
}

let enc = JSONEncoder()
enc.outputFormatting = [.sortedKeys]
let out = Output(source: pdfURL.lastPathComponent, engine: "Apple Vision VNRecognizeTextRequest (accurate, en-US)", pages: pages)
try enc.encode(out).write(to: outURL)
print("ocr: \(pages.count) pages, \(pages.reduce(0) { $0 + $1.lines.count }) lines -> \(outURL.path)")
