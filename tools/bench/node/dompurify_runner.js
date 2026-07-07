// Sanitize stdin with DOMPurify, the competitor for turbohtml's sanitizer. The mode (argv[2]) picks the config the
// bench (tools/bench/competitors/dompurify.py) is timing: "templates" turns on SAFE_FOR_TEMPLATES (the oracle for
// strip_template_markers), "named-props" turns on SANITIZE_NAMED_PROPS (the oracle for isolate_named_props). One
// document is piped in and the sanitized result is read back.
const DOMPurify = require("isomorphic-dompurify");

const CONFIGS = {
  templates: { SAFE_FOR_TEMPLATES: true },
  "named-props": { SANITIZE_NAMED_PROPS: true },
};
const config = CONFIGS[process.argv[2] || "templates"];

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
});
process.stdin.on("end", () => {
  process.stdout.write(DOMPurify.sanitize(input, config));
});
