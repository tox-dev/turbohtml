// jsdom oracle for the DOM differential suite (tests/conformance/test_dom_jsdom_differential.py). jsdom passes almost
// all of WPT dom/, so it is the cross-language reference the same operation sequence is replayed against; the Python
// side replays it through turbohtml and the two results must agree byte/structure-exact. One JSON array of cases is
// piped in on stdin; a JSON array of results (one per case, keyed by id) is written back. The op vocabulary here is
// mirrored line-for-line by the Python harness, so a divergence is a real turbohtml/jsdom disagreement, never a
// runner asymmetry. Node addressing is a child-index path from documentElement (or a fragment root); both parsers
// build the same WHATWG tree, so the paths line up.
const { JSDOM } = require("jsdom");

function nodeAt(root, path) {
  let node = root;
  for (const index of path) node = node.childNodes[index];
  return node;
}

function desc(node) {
  if (node === null || node === undefined) return "null";
  switch (node.nodeType) {
    case 1: {
      const id = node.getAttribute("id");
      const slot = node.getAttribute("slot");
      const tag = node.tagName.toLowerCase();
      return "E[" + tag + (id ? "#" + id : "") + (slot ? "@" + slot : "") + "]";
    }
    case 3:
      return "T[" + node.data + "]";
    case 8:
      return "C[" + node.data + "]";
    case 11:
      return "FRAG";
    default:
      return "N" + node.nodeType;
  }
}

// Serialize a fragment through the HTML serializer exactly as the light-tree serializer would: dropping its children
// into a holder and reading innerHTML runs the spec algorithm (escaping, void elements, raw-text) rather than a
// hand-rolled concat that could disagree with turbohtml on escaping.
function fragHtml(frag, document) {
  const holder = document.createElement("div");
  holder.appendChild(frag);
  return holder.innerHTML;
}

const FILTERS = {
  accept_all: () => 1,
  reject_nav: (node) => (node.nodeType === 1 && node.tagName.toLowerCase() === "nav" ? 2 : 1),
  skip_nav: (node) => (node.nodeType === 1 && node.tagName.toLowerCase() === "nav" ? 3 : 1),
  reject_div: (node) => (node.nodeType === 1 && node.tagName.toLowerCase() === "div" ? 2 : 1),
  skip_div: (node) => (node.nodeType === 1 && node.tagName.toLowerCase() === "div" ? 3 : 1),
  reject_ul: (node) => (node.nodeType === 1 && node.tagName.toLowerCase() === "ul" ? 2 : 1),
  skip_span: (node) => (node.nodeType === 1 && node.tagName.toLowerCase() === "span" ? 3 : 1),
  only_a: (node) => (node.nodeType === 1 && node.tagName.toLowerCase() === "a" ? 1 : 3),
  reject_section_skip_p: (node) => {
    const tag = node.nodeType === 1 ? node.tagName.toLowerCase() : "";
    if (tag === "section") return 2;
    if (tag === "p") return 3;
    return 1;
  },
};

function makeNode(spec, document) {
  if (spec.text !== undefined) return document.createTextNode(spec.text);
  const element = document.createElement(spec.tag);
  if (spec.html !== undefined) element.innerHTML = spec.html;
  return element;
}

function buildRange(kase, root, document) {
  const range = document.createRange();
  const [startPath, startOffset] = kase.start;
  const [endPath, endOffset] = kase.end;
  range.setStart(nodeAt(root, startPath), startOffset);
  range.setEnd(nodeAt(root, endPath), endOffset);
  return range;
}

function runRange(kase) {
  const dom = new JSDOM(kase.html);
  const document = dom.window.document;
  const root = document.documentElement;
  const range = buildRange(kase, root, document);
  switch (kase.probe) {
    case "boundaries":
      return {
        collapsed: range.collapsed,
        ancestor: desc(range.commonAncestorContainer),
        start: desc(range.startContainer) + ":" + range.startOffset,
        end: desc(range.endContainer) + ":" + range.endOffset,
      };
    case "clone":
      return { frag: fragHtml(range.cloneContents(), document), doc: root.outerHTML };
    case "extract": {
      const frag = fragHtml(range.extractContents(), document);
      return { frag, doc: root.outerHTML, collapsed: range.collapsed };
    }
    case "delete":
      range.deleteContents();
      return { doc: root.outerHTML, collapsed: range.collapsed };
    case "insert":
      range.insertNode(makeNode(kase.node, document));
      return { doc: root.outerHTML };
    case "surround":
      range.surroundContents(makeNode(kase.wrapper, document));
      return { doc: root.outerHTML };
    case "compare_boundary": {
      const other = document.createRange();
      other.setStart(nodeAt(root, kase.other_start[0]), kase.other_start[1]);
      other.setEnd(nodeAt(root, kase.other_end[0]), kase.other_end[1]);
      return { value: range.compareBoundaryPoints(kase.how, other) };
    }
    case "compare_point":
      return { value: range.comparePoint(nodeAt(root, kase.point[0]), kase.point[1]) };
    case "point_in_range":
      return { value: range.isPointInRange(nodeAt(root, kase.point[0]), kase.point[1]) };
    case "intersects":
      return { value: range.intersectsNode(nodeAt(root, kase.node_path)) };
    case "select_node": {
      const target = document.createRange();
      target.selectNode(nodeAt(root, kase.node_path));
      return {
        start: desc(target.startContainer) + ":" + target.startOffset,
        end: desc(target.endContainer) + ":" + target.endOffset,
      };
    }
    case "select_node_contents": {
      const target = document.createRange();
      target.selectNodeContents(nodeAt(root, kase.node_path));
      return {
        start: desc(target.startContainer) + ":" + target.startOffset,
        end: desc(target.endContainer) + ":" + target.endOffset,
      };
    }
    default:
      throw new Error("unknown range probe " + kase.probe);
  }
}

function runTraversal(kase) {
  const dom = new JSDOM(kase.html);
  const document = dom.window.document;
  const root = nodeAt(document.documentElement, kase.root);
  const filter = kase.filter ? { acceptNode: FILTERS[kase.filter] } : null;
  if (kase.kind === "walker") {
    const walker = document.createTreeWalker(root, kase.what, filter);
    return { nodes: runWalker(walker, kase.probe) };
  }
  const iterator = document.createNodeIterator(root, kase.what, filter);
  return { nodes: runIterator(iterator, kase.probe) };
}

function runWalker(walker, probe) {
  const out = [];
  if (probe === "forward") {
    let node = walker.nextNode();
    while (node) {
      out.push(desc(node));
      node = walker.nextNode();
    }
  } else if (probe === "backward") {
    while (walker.nextNode()) {
      /* advance to the last accepted node */
    }
    let node = walker.previousNode();
    while (node) {
      out.push(desc(node));
      node = walker.previousNode();
    }
  } else if (probe === "children") {
    let node = walker.firstChild();
    while (node) {
      out.push(desc(node));
      node = walker.nextSibling();
    }
  } else if (probe === "last_child_parents") {
    let node = walker.lastChild();
    while (node) {
      out.push(desc(node));
      node = walker.parentNode();
    }
  }
  return out;
}

function runIterator(iterator, probe) {
  const out = [];
  if (probe === "forward") {
    let node = iterator.nextNode();
    while (node) {
      out.push(desc(node));
      node = iterator.nextNode();
    }
  } else if (probe === "backward") {
    while (iterator.nextNode()) {
      /* advance past the end */
    }
    let node = iterator.previousNode();
    while (node) {
      out.push(desc(node));
      node = iterator.previousNode();
    }
  }
  return out;
}

function runShadow(kase) {
  const dom = new JSDOM("<!doctype html><html><head></head><body></body></html>");
  const document = dom.window.document;
  if (kase.probe === "declarative") {
    const inner = new JSDOM(kase.html);
    const host = inner.window.document.getElementById(kase.host_id);
    const shadow = host ? host.shadowRoot : null;
    return {
      has_shadow: shadow !== null,
      shadow_html: shadow ? shadow.innerHTML : null,
      light_html: host ? host.innerHTML : null,
    };
  }
  const holder = document.createElement("div");
  holder.innerHTML = kase.host_html;
  const host = holder.firstElementChild;
  document.body.appendChild(host);
  const shadow = host.attachShadow({ mode: kase.mode });
  shadow.innerHTML = kase.shadow_html;
  if (kase.probe === "attach_mode") {
    return { reachable: host.shadowRoot !== null, mode: shadow.mode };
  }
  if (kase.probe === "assigned") {
    const slots = {};
    for (const [name, selector] of Object.entries(kase.slots)) {
      const slot = shadow.querySelector(selector);
      slots[name] = {
        nodes: slot.assignedNodes().map(desc),
        elements: slot.assignedElements().map(desc),
        flatten: slot.assignedNodes({ flatten: true }).map(desc),
      };
    }
    const children = Array.from(host.childNodes).map((child) => (child.assignedSlot ? desc(child.assignedSlot) : "null"));
    return { slots, children };
  }
  throw new Error("unknown shadow probe " + kase.probe);
}

function runCase(kase) {
  if (kase.feature === "range") return runRange(kase);
  if (kase.feature === "traversal") return runTraversal(kase);
  if (kase.feature === "shadow") return runShadow(kase);
  throw new Error("unknown feature " + kase.feature);
}

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
});
process.stdin.on("end", () => {
  const cases = JSON.parse(input);
  const results = cases.map((kase) => {
    try {
      return { id: kase.id, ok: true, result: runCase(kase) };
    } catch (error) {
      return { id: kase.id, ok: false, error: String(error && error.message ? error.message : error) };
    }
  });
  process.stdout.write(JSON.stringify(results));
});
