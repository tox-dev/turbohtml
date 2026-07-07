// The getComputedStyle oracle for turbohtml.cssom.computed_style (issue #546), used by the differential conformance
// test tests/conformance/test_cssom_jsdom_conformance.py. jsdom passes most of WPT's CSSOM suite, so its cascade +
// getComputedStyle is a strong reference for the author-origin cascade turbohtml resolves. One JSON array of cases is
// piped in ([{id, html, targets: [{selector, props}]}]); for each target element this loads the document in jsdom,
// resolves getComputedStyle, and reads getPropertyValue for each property, writing back the parallel results as
// {id: {selector: {prop: value}}}. The whole corpus goes through a single process so the run stays cheap.
const { JSDOM } = require("jsdom");

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => {
  input += chunk;
});
process.stdin.on("end", () => {
  const cases = JSON.parse(input);
  const out = {};
  for (const testCase of cases) {
    const dom = new JSDOM(testCase.html);
    const { window } = dom;
    const byTarget = {};
    for (const target of testCase.targets) {
      const element =
        target.selector === ":root"
          ? window.document.documentElement
          : window.document.querySelector(target.selector);
      const values = {};
      if (element !== null) {
        const style = window.getComputedStyle(element);
        for (const prop of target.props) {
          values[prop] = style.getPropertyValue(prop);
        }
      }
      byTarget[target.selector] = values;
    }
    out[testCase.id] = byTarget;
  }
  process.stdout.write(JSON.stringify(out));
});
