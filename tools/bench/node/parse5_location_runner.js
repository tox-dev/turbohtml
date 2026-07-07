// Emit parse5's sourceCodeLocationInfo for every element in a document, the oracle for turbohtml's
// parse(source_locations=True). Reads one HTML document from stdin, writes one JSON object to stdout:
// { elements: [ { key, tag, startTag, endTag, attrs }, ... ] } where each span is
// [startLine, startCol, startOffset, endLine, endCol, endOffset]. parse5 columns are 1-based; the Python
// side maps turbohtml's 0-based column with +1. Elements parse5 implies (no start tag in the source) carry
// a null sourceCodeLocation and are skipped, matching what the differential can align. The key is the
// start-tag start offset, an unambiguous anchor for pairing the two trees element-for-element.
const parse5 = require("parse5");

function span(loc) {
  if (!loc) return null;
  return [loc.startLine, loc.startCol, loc.startOffset, loc.endLine, loc.endCol, loc.endOffset];
}

const elements = [];

function walk(node) {
  if (node.tagName !== undefined) {
    const loc = node.sourceCodeLocation;
    if (loc && loc.startTag) {
      const attrs = {};
      if (loc.attrs) {
        for (const [name, attrLoc] of Object.entries(loc.attrs)) {
          attrs[name] = span(attrLoc);
        }
      }
      elements.push({
        key: loc.startTag.startOffset,
        tag: node.tagName,
        startTag: span(loc.startTag),
        endTag: span(loc.endTag),
        attrs,
      });
    }
  }
  for (const child of node.childNodes || []) walk(child);
}

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
});
process.stdin.on("end", () => {
  const document = parse5.parse(input, { sourceCodeLocationInfo: true });
  walk(document);
  process.stdout.write(JSON.stringify({ elements }));
});
