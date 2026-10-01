// Second independent re-parser for the sanitizer mutation-freedom oracle (tools/fuzz/sanitize_oracles.py). Reads a JSON
// array of HTML strings on stdin, parses each as a <div> fragment with scripting on (what innerHTML does), and writes a
// JSON array of html5lib-tests tree dumps. The dump mirrors parse5's own test serializer
// (tests/conformance/parse5/test/utils/serialize-to-dat-file-format.ts), which is TypeScript and not shipped in the npm
// package, so the Python side compares parse5, html5lib-python and turbohtml in one format.
const { defaultTreeAdapter: adapter, html, parseFragment } = require("parse5");

const PREFIXES = { [html.NS.SVG]: "svg ", [html.NS.MATHML]: "math " };

function dump(nodes, indent, out) {
  const pad = "|".padEnd(indent + 2, " ");
  for (let node of nodes) {
    if (adapter.isCommentNode(node)) {
      out.push(`${pad}<!-- ${adapter.getCommentNodeContent(node)} -->`);
    } else if (adapter.isTextNode(node)) {
      out.push(`${pad}"${adapter.getTextNodeContent(node)}"`);
    } else {
      const tag = adapter.getTagName(node);
      const namespace = adapter.getNamespaceURI(node);
      out.push(`${pad}<${PREFIXES[namespace] ?? ""}${tag}>`);
      let childIndent = indent + 2;
      const attrPad = "|".padEnd(childIndent + 2, " ");
      out.push(
        ...adapter
          .getAttrList(node)
          .map((attr) => `${attrPad}${attr.prefix ? `${attr.prefix} ` : ""}${attr.name}="${attr.value}"`)
          .sort(),
      );
      if (tag === "template" && namespace === html.NS.HTML) {
        out.push(`${attrPad}content`);
        childIndent += 2;
        node = adapter.getTemplateContent(node);
      }
      dump(adapter.getChildNodes(node), childIndent, out);
    }
  }
  return out;
}

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
});
process.stdin.on("end", () => {
  const dumps = JSON.parse(input).map((markup) => {
    const context = adapter.createElement("div", html.NS.HTML, []);
    return dump(adapter.getChildNodes(parseFragment(context, markup, { scriptingEnabled: true })), 0, []).join("\n");
  });
  process.stdout.write(JSON.stringify(dumps));
});
