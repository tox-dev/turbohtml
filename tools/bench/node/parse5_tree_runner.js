// Second independent re-parser for the sanitizer mutation-freedom oracle (tools/fuzz/sanitize_oracles.py). Reads a JSON
// array of {markup, omit} objects on stdin, parses each markup as a <div> fragment with scripting on (what innerHTML
// does), and writes a JSON array of html5lib-tests tree dumps. ``omit`` lists the implied table wrapper tags (tbody, tr,
// colgroup) the sanitizer's policy leaves out: parse5 re-creates each one on re-parse, so the dump drops it and renders
// its children in its place, as sanitize.c does for the output. The dump mirrors parse5's own test serializer
// (tests/conformance/parse5/test/utils/serialize-to-dat-file-format.ts), which is TypeScript and not shipped in the npm
// package, so the Python side compares parse5, html5lib-python and turbohtml in one format.
const { defaultTreeAdapter: adapter, html, parseFragment } = require("parse5");

const PREFIXES = { [html.NS.SVG]: "svg ", [html.NS.MATHML]: "math " };
const IMPLIED_WRAPPERS = new Set(["tbody table", "tr tbody", "colgroup table"]);

function omittedWrapper(node, parent, omit) {
  if (parent === null || adapter.isTextNode(node) || adapter.isCommentNode(node)) {
    return false;
  }
  const tag = adapter.getTagName(node);
  return (
    adapter.getNamespaceURI(node) === html.NS.HTML &&
    adapter.getAttrList(node).length === 0 &&
    omit.includes(tag) &&
    IMPLIED_WRAPPERS.has(`${tag} ${adapter.getTagName(parent)}`)
  );
}

function effectiveChildren(nodes, parent, omit) {
  return nodes.flatMap((node) =>
    omittedWrapper(node, parent, omit) ? effectiveChildren(adapter.getChildNodes(node), node, omit) : [node],
  );
}

function dump(nodes, indent, out, parent, omit) {
  const pad = "|".padEnd(indent + 2, " ");
  let text = "";
  const flush = () => {
    if (text !== "") {
      out.push(`${pad}"${text}"`);
      text = "";
    }
  };
  for (let node of effectiveChildren(nodes, parent, omit)) {
    if (adapter.isTextNode(node)) {
      text += adapter.getTextNodeContent(node);
      continue;
    }
    flush();
    if (adapter.isCommentNode(node)) {
      out.push(`${pad}<!-- ${adapter.getCommentNodeContent(node)} -->`);
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
        dump(adapter.getChildNodes(node), childIndent, out, null, omit);
      } else {
        dump(adapter.getChildNodes(node), childIndent, out, node, omit);
      }
    }
  }
  flush();
  return out;
}

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
});
process.stdin.on("end", () => {
  const dumps = JSON.parse(input).map(({ markup, omit }) => {
    const context = adapter.createElement("div", html.NS.HTML, []);
    const fragment = adapter.getChildNodes(parseFragment(context, markup, { scriptingEnabled: true }));
    return dump(fragment, 0, [], null, omit).join("\n");
  });
  process.stdout.write(JSON.stringify(dumps));
});
