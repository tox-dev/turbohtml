// Parse stdin with parse5's sourceCodeLocationInfo on, the competitor for turbohtml's parse(source_locations=True).
// The bench (tools/bench/competitors/parse5.py) pipes one document in and reads back the root element's tag, so the
// full parse-and-locate pass is what gets timed.
const parse5 = require("parse5");

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
});
process.stdin.on("end", () => {
  const document = parse5.parse(input, { sourceCodeLocationInfo: true });
  process.stdout.write(String(document.childNodes.length));
});
