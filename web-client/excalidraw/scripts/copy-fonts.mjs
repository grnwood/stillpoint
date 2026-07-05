import { cp, mkdir, rm } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const projectRoot = resolve(here, "..");
const packageRoot = resolve(projectRoot, "..", "node_modules", "@excalidraw", "excalidraw");
const source = resolve(packageRoot, "dist", "prod", "fonts");
const target = resolve(projectRoot, "public", "fonts");

await rm(target, { recursive: true, force: true });
await mkdir(dirname(target), { recursive: true });
await cp(source, target, { recursive: true });
