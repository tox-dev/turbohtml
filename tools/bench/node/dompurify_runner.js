// Sanitize stdin with DOMPurify's SAFE_FOR_TEMPLATES on, the competitor for turbohtml's strip_template_markers.
// The bench (tools/bench/competitors/dompurify.py) pipes one document in and reads the sanitized result back.
const DOMPurify = require("isomorphic-dompurify");

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
});
process.stdin.on("end", () => {
  process.stdout.write(DOMPurify.sanitize(input, { SAFE_FOR_TEMPLATES: true }));
});
