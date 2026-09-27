/**
 * 预渲染：site-data.json → dist/*.html
 *
 * 不用 vite-plugin-ssr / vite-ssg / Astro（decisions.md C11 改写二）：
 * vite 只负责把 TSX 编译成 JS、把 CSS 收成单文件，HTML 由这个脚本自己出。
 * 产物是纯静态文件，file:// 直接能开。
 *
 * 数据与产物路径从环境变量来（MEMEX_SITE_DATA / MEMEX_SITE_OUT），命令行参数可覆盖：
 *   node build/prerender.mjs [data.json] [outDir]
 * 这样 package.json 里的 build 脚本不必写死任何路径，Python 侧也只需注入环境。
 */
import { readFileSync, writeFileSync, mkdirSync, readdirSync, existsSync } from "node:fs";
import { join, dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
// 可被 MEMEX_WEB_ROOT 覆盖：测试要能造一个「没有 dist/assets」的场景来验证守卫，
// 否则「CSS 缺失必须报错」这条就永远测不到。
const webRoot = process.env.MEMEX_WEB_ROOT
  ? resolve(process.env.MEMEX_WEB_ROOT)
  : join(here, "..");

const EXPECTED_SCHEMA = "memex/site/1";

async function loadRenderer() {
  // vite build 产出 dist/assets/site.js（ESM，react 保持 external）
  const candidates = [
    join(webRoot, "dist", "assets", "site.js"),
    join(webRoot, "dist", "site.js"),
  ];
  const found = candidates.find((p) => existsSync(p));
  if (!found) {
    throw new Error(
      `找不到 vite 产物（试过 ${candidates.join(", ")}）。先跑 vite build。`,
    );
  }
  return import(pathToFileUrl(found));
}

function pathToFileUrl(p) {
  return new URL(`file://${p}`).href;
}

function readCss() {
  // 拿不到 CSS 就抛错，绝不返回空串：返回空串会让下面每页都写出裸 HTML，
  // 而产物照样「构建成功」。曾经就这么丢过一次样式——入口忘了 import styles.css，
  // dist/assets 压根不存在，守卫因为「没 CSS 可校验」而全部放行。
  const assetsDir = join(webRoot, "dist", "assets");
  if (!existsSync(assetsDir)) {
    throw new Error(
      `${assetsDir} 不存在。web/src/render.tsx 必须 import "./styles.css"，` +
        "否则 vite 不会产出 CSS，页面会是没有样式的裸 HTML。",
    );
  }
  const css = readdirSync(assetsDir).find((f) => f.endsWith(".css"));
  if (!css) {
    throw new Error(
      `${assetsDir} 里没有 .css。同上：入口没 import 样式文件，构建出的是裸页面。`,
    );
  }
  return readFileSync(join(assetsDir, css), "utf8");
}

async function main() {
  const dataPath = process.argv[2] || process.env.MEMEX_SITE_DATA;
  const outDir = process.argv[3] || process.env.MEMEX_SITE_OUT;
  if (!dataPath || !outDir) {
    throw new Error(
      "缺少 site-data.json 或产物目录：设 MEMEX_SITE_DATA / MEMEX_SITE_OUT，或直接传两个位置参数",
    );
  }

  const data = JSON.parse(readFileSync(dataPath, "utf8"));
  if (data.schema_id !== EXPECTED_SCHEMA) {
    // 不猜、不兼容：schema 对不上就停，宁可不产页面也不要出一张错页
    throw new Error(
      `site-data.json 的 schema_id 是 ${data.schema_id}，预渲染器只认 ${EXPECTED_SCHEMA}。` +
        "先改 web/src/types.ts 与 docs，再改 dump.py。",
    );
  }

  const mod = await loadRenderer();
  const { renderPage, CSS_PLACEHOLDER } = mod;
  const css = readCss();

  const inline = (html) => {
    // 先替换再验：占位符在替换**前**当然存在，只有替换**后**还在才说明页面是裸的。
    const out = html.replace(CSS_PLACEHOLDER, css);
    if (out.includes(CSS_PLACEHOLDER)) {
      throw new Error("CSS_PLACEHOLDER 替换后仍留在页面里，样式不会生效。");
    }
    if (css && !out.includes("<style>")) {
      throw new Error("页面里没有 style 标签，CSS 无处安放。");
    }
    return out;
  };

  mkdirSync(outDir, { recursive: true });

  const written = [];
  const write = (name, which, arg, repo) => {
    const html = renderPage(which, arg, repo);
    writeFileSync(join(outDir, name), inline(html), "utf8");
    written.push(name);
  };

  write("index.html", "index", data);
  write("patterns.html", "patterns", data);
  for (const repo of data.repos) {
    write(repo.file, "repo", data, repo);
  }

  // 校验：页面数必须等于目录数 + 模式页，否则说明有仓没落页——不静默少一页
  const expect = data.repos.length + 2;
  if (written.length !== expect) {
    throw new Error(`预期 ${expect} 个页面，实际写出 ${written.length} 个：${written.join(", ")}`);
  }

  process.stdout.write(
    `预渲染完成：${written.length} 个页面 → ${outDir}\n` +
      `  索引 ${data.stats.repos} 仓 / ${data.stats.cards} 卡 / ${data.stats.patterns} 模式\n`,
  );
}

main().catch((e) => {
  process.stderr.write(`预渲染失败：${e && e.message ? e.message : e}\n`);
  process.exit(1);
});
