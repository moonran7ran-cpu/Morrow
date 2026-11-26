import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const target = path.join(
  here,
  "node_modules",
  "@agentclientprotocol",
  "codex-acp",
  "dist",
  "index.js",
);

let source = fs.readFileSync(target, "utf8");
const marker = "MORROW_PATCH_BASE_INSTRUCTIONS_OVERRIDE";
if (source.includes(marker)) {
  process.stdout.write("codex-acp Morrow override patch already applied\n");
  process.exit(0);
}

function replaceOnce(oldText, newText, label) {
  const index = source.indexOf(oldText);
  if (index < 0) {
    throw new Error("codex-acp patch anchor missing: " + label);
  }
  if (source.indexOf(oldText, index + oldText.length) >= 0) {
    throw new Error("codex-acp patch anchor is not unique: " + label);
  }
  source = source.replace(oldText, newText);
}

replaceOnce(
  '!("mode" in value) || value.mode !== "append" || !("content" in value)',
  '!("mode" in value) || value.mode !== "append" && value.mode !== "override" || !("content" in value)',
  "accept override mode",
);

replaceOnce(
  '"systemPrompt requires append mode and text content"',
  '"systemPrompt requires append or override mode and text content"',
  "validation message",
);

replaceOnce(
  'return text === void 0 ? null : { mode: "append", content: [{ type: "text", text }] };',
  'return text === void 0 ? null : { mode: value?.mode === "override" ? "override" : "append", content: [{ type: "text", text }] };',
  "preserve system prompt mode",
);

replaceOnce(
  `  async newSession(request) {
    const instructions = await this.resolveSessionInstructions(request, void 0, true);
    const additionalDirectories = readAdditionalDirectories(request.cwd, request.additionalDirectories, request._meta);
    const developerInstructions = instructions?.developerInstructions;`,
  `  async newSession(request) {
    // MORROW_PATCH_BASE_INSTRUCTIONS_OVERRIDE
    const instructions = await this.resolveSessionInstructions(request, void 0, true);
    const additionalDirectories = readAdditionalDirectories(request.cwd, request.additionalDirectories, request._meta);
    const overrideBaseInstructions = request.systemPrompt?.mode === "override" ? clientSystemPromptText(request.systemPrompt) : void 0;
    const developerInstructions = overrideBaseInstructions === void 0 ? instructions?.developerInstructions : void 0;`,
  "new session override selection",
);

replaceOnce(
  `      cwd: request.cwd,
      ...developerInstructions !== void 0 && { developerInstructions }
    });`,
  `      cwd: request.cwd,
      ...overrideBaseInstructions !== void 0 && { baseInstructions: overrideBaseInstructions },
      ...developerInstructions !== void 0 && { developerInstructions }
    });`,
  "threadStart baseInstructions",
);

fs.writeFileSync(target, source);
process.stdout.write("applied Morrow baseInstructions override patch to codex-acp 2.12.0\n");
