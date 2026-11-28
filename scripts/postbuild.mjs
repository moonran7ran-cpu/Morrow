import { cp, copyFile, mkdir } from "node:fs/promises";
await mkdir("dist/static", { recursive: true });
await cp("static", "dist/static", { recursive: true });
await copyFile("mock-api.js", "dist/mock-api.js");
