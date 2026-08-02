import * as esbuild from "esbuild";
import { readFileSync, writeFileSync, existsSync, readdirSync, mkdirSync } from "fs";
import path from "path";

async function buildCSS() {
  // Collect all CSS files from static/css/ and static/ (excluding standalone SpiderFoot CSS)
  const cssFiles = [];
  for (const dir of ["static/css", "static"]) {
    if (!existsSync(dir)) continue;
    for (const f of readdirSync(dir)) {
      if (f.endsWith(".css") && f !== "style.css" && f !== "cms-professional.css") cssFiles.push(path.join(dir, f));
    }
  }

  if (cssFiles.length === 0) {
    console.log("⚠  No CSS files found");
    return;
  }

  // Minify each file individually, then concatenate
  const parts = [];
  for (const file of cssFiles) {
    const result = await esbuild.build({
      entryPoints: [file],
      minify: true,
      write: false,
      logLevel: "warning",
    });
    parts.push(result.outputFiles[0].text);
  }

  const combined = parts.join("\n");
  writeFileSync("static/dist/bundle.min.css", combined);

  const version = String(Date.now());
  writeFileSync("static/dist/.css_version", version);
  console.log(
    `✓ CSS bundle: ${(combined.length / 1024).toFixed(1)} KB (${cssFiles.length} files, version: ${version.slice(-6)})`,
  );
}

async function buildJS() {
  const jsFile = "static/js/base.js";
  if (!existsSync(jsFile)) {
    console.log("⚠  static/js/base.js not found, skipping JS build");
    return;
  }

  const result = await esbuild.build({
    entryPoints: [jsFile],
    minify: true,
    outfile: "static/dist/base.min.js",
    logLevel: "warning",
    write: true,
  });

  const jsContent = readFileSync("static/dist/base.min.js", "utf-8");
  const original = readFileSync(jsFile, "utf-8");
  const savings = ((original.length - jsContent.length) / original.length * 100).toFixed(0);
  console.log(`✓ JS bundle: ${(jsContent.length / 1024).toFixed(1)} KB (${savings}% smaller)`);
}

async function main() {
  mkdirSync("static/dist", { recursive: true });
  await buildCSS();
  await buildJS();
  let total = 0;
  for (const f of ["static/dist/bundle.min.css", "static/dist/base.min.js"]) {
    if (existsSync(f)) total += readFileSync(f, "utf-8").length;
  }
  console.log(`  Total: ${(total / 1024).toFixed(1)} KB (minified)`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
