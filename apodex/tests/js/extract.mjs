import fs from "node:fs";
import vm from "node:vm";
import path from "node:path";
import { fileURLToPath } from "node:url";

export function loadPureClient() {
  const htmlPath = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../web_static/index.html");
  const html = fs.readFileSync(htmlPath, "utf8");
  const match = html.match(/\/\* FA_PURE_START \*\/([\s\S]*?)\/\* FA_PURE_END \*\//);
  if (!match) throw new Error("missing FA_PURE_START region");
  const sandbox = { console };
  vm.createContext(sandbox);
  vm.runInContext(match[1], sandbox);
  return sandbox;
}
