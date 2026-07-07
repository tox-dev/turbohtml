// Batch oracle for the DOMPurify conformance differential (tests/conformance/test_sanitizer_dompurify_conformance.py).
// Reads one JSON object {"mode": <name>, "inputs": [<html>, ...]} on stdin and writes a JSON array of DOMPurify's
// sanitized output for each input under the config that <mode> selects. One process handles a whole corpus so the
// Python side pays a single node spawn per mode instead of one per case. The modes mirror the Policy features the
// harness validates head-to-head: SAFE_FOR_TEMPLATES (strip_template_markers), SANITIZE_NAMED_PROPS
// (isolate_named_props), CUSTOM_ELEMENT_HANDLING (custom_element_check/custom_attribute_check), and the three
// USE_PROFILES namespace gates (allow_html/allow_svg/allow_mathml).
const DOMPurify = require("isomorphic-dompurify");

const CONFIGS = {
  default: {},
  templates: { SAFE_FOR_TEMPLATES: true },
  "named-props": { SANITIZE_NAMED_PROPS: true },
  "custom-elements": {
    CUSTOM_ELEMENT_HANDLING: {
      tagNameCheck: /^x-/,
      attributeNameCheck: /^data-/,
      allowCustomizedBuiltInElements: false,
    },
  },
  "profile-html": { USE_PROFILES: { html: true } },
  "profile-svg": { USE_PROFILES: { svg: true } },
  "profile-mathml": { USE_PROFILES: { mathMl: true } },
};

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
});
process.stdin.on("end", () => {
  const request = JSON.parse(input);
  const config = CONFIGS[request.mode];
  if (config === undefined) {
    process.stderr.write(`unknown mode: ${request.mode}\n`);
    process.exit(1);
  }
  const outputs = request.inputs.map((html) => DOMPurify.sanitize(html, config));
  process.stdout.write(JSON.stringify(outputs));
});
