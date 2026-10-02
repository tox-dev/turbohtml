// Independent JavaScript reader for the identifier/string-set oracle (tools/fuzz/round_trip_oracles.py). Reads one
// JSON string of source per stdin line and answers one JSON line: null when acorn rejects it as a script and as a
// module, else the sorted free identifier names and static strings. A free identifier is a reference eslint-scope
// leaves in the global scope's `through` list: no declaration anywhere in the program binds it, so ResolveBinding
// reaches the global object (ECMA-262 9.1.2.1 GetIdentifierReference). The static strings are every string literal
// value, every untagged template's cooked text and tagged template's raw text, and every non-computed property name as
// its PropertyKey string (ECMA-262 13.2.5.4: an identifier's StringValue, a numeric key through ToString), so `a["b"]`
// and `a.b`, or `{"1": x}` and `{1: x}`, read the same.
const acorn = require("acorn");
const eslintScope = require("eslint-scope");
const readline = require("node:readline");

function parse(source) {
  for (const sourceType of ["script", "module"]) {
    try {
      return [acorn.parse(source, { ecmaVersion: "latest", sourceType, ranges: true }), sourceType];
    } catch {
      // the other goal symbol may accept it
    }
  }
  return [null, null];
}

function propertyKey(node) {
  return node.type === "Identifier" ? node.name : node.type === "Literal" ? String(node.value) : null;
}

function strings(root) {
  const found = new Set();
  const stack = [[root, null]];
  while (stack.length > 0) {
    const [node, parent] = stack.pop();
    if (node.type === "Literal" && typeof node.value === "string") {
      found.add(node.value);
    } else if (node.type === "TemplateElement") {
      const tagged = parent !== null && parent.type === "TaggedTemplateExpression";
      found.add(tagged || node.value.cooked === null ? `raw:${node.value.raw}` : node.value.cooked);
    } else if (
      (node.type === "Property" || node.type === "MethodDefinition" || node.type === "PropertyDefinition") &&
      !node.computed
    ) {
      const key = propertyKey(node.key);
      if (key !== null) found.add(key);
    } else if (node.type === "MemberExpression" && !node.computed && node.property.type === "Identifier") {
      found.add(node.property.name);
    }
    const next = node.type === "TemplateLiteral" && parent?.type === "TaggedTemplateExpression" ? parent : node;
    for (const value of Object.values(node)) {
      for (const child of Array.isArray(value) ? value : [value]) {
        if (child !== null && typeof child === "object" && typeof child.type === "string") stack.push([child, next]);
      }
    }
  }
  return [...found].sort();
}

function names(source) {
  const [ast, sourceType] = parse(source);
  if (ast === null) return null;
  const manager = eslintScope.analyze(ast, { ecmaVersion: 2022, sourceType, fallback: "iteration" });
  const free = new Set(manager.globalScope.through.map((reference) => reference.identifier.name));
  return { sourceType, free: [...free].sort(), strings: strings(ast) };
}

// eslint-scope throws on a few trees it cannot scope; that answer is "unknown", which the Python side skips
function answer(source) {
  try {
    return names(source);
  } catch (error) {
    return { error: String(error) };
  }
}

readline.createInterface({ input: process.stdin }).on("line", (line) => {
  process.stdout.write(`${JSON.stringify(answer(JSON.parse(line)))}\n`);
});
