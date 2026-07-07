// Sanitize stdin with sanitize-html's transformTags renaming the deprecated presentational tags, the competitor for
// turbohtml's Policy.transform_tags. The bench (tools/bench/competitors/sanitize_html.py) pipes one document in and
// reads the sanitized result back. The allowlist and rename map mirror the turbohtml transform op in tools/bench/core.py.
const sanitizeHtml = require("sanitize-html");

const OPTIONS = {
  allowedTags: [
    "a", "abbr", "b", "big", "blockquote", "center", "code", "div", "em", "font", "h1", "h2", "h3", "h4", "h5", "h6",
    "i", "li", "ol", "p", "s", "span", "strike", "strong", "tt", "ul",
  ],
  allowedAttributes: { div: ["class"], a: ["href", "title"] },
  transformTags: {
    b: "strong",
    i: "em",
    big: "span",
    tt: "code",
    strike: "s",
    font: "span",
    center: sanitizeHtml.simpleTransform("div", { class: "center" }),
  },
};

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
});
process.stdin.on("end", () => {
  process.stdout.write(sanitizeHtml(input, OPTIONS));
});
