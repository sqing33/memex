import { renderToStaticMarkup } from "react-dom/server";
import type { ReactElement } from "react";
import { IndexPage } from "./pages/IndexPage";
import { PatternsPage } from "./pages/PatternsPage";
import { RepoPage } from "./pages/RepoPage";
import { dash } from "./components/ui";
import type { Repo, SiteData } from "./types";

/**
 * 页面外壳。预渲染而不是 SPA（tech-design.md §2.7.2）：逐页 renderToStaticMarkup
 * 出完整 HTML，产物 web/dist/*.html 可以 file:// 直接打开，不做客户端路由。
 */
function Layout({
  title,
  data,
  body,
}: {
  title: string;
  data: SiteData;
  body: ReactElement;
}) {
  return (
    <html lang="zh-CN">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <meta name="generator" content={`memex export-site · ${data.schema_id}`} />
        <title>{title}</title>
      </head>
      <body>
        {body}
        <footer className="muted small">
          由 memex export-site 生成（{data.schema_id}） · 索引 embedder {dash(data.embedder)} ·
          生成时间 {data.generated_at}
        </footer>
      </body>
    </html>
  );
}

/** CSS 占位：产物要单文件可离线开，样式必须内联。prerender.mjs 负责填真 CSS */
export const CSS_PLACEHOLDER = "/*__MEMEX_CSS__*/";

function renderShell(title: string, data: SiteData, body: ReactElement): string {
  const html = renderToStaticMarkup(<Layout title={title} data={data} body={body} />);
  return "<!DOCTYPE html>" + html.replace("</head>", `<style>${CSS_PLACEHOLDER}</style></head>`);
}

/**
 * 供 build/prerender.mjs 调用，返回完整 HTML 字符串。
 * 三种页面共用同一份 SiteData——单仓页的页脚要显示真实的 embedder 与生成时间，
 * 不给它造一份缺字段的替身。
 */
export function renderPage(
  which: "index" | "patterns",
  data: SiteData,
): string;
export function renderPage(which: "repo", data: SiteData, repo: Repo): string;
export function renderPage(
  which: "index" | "repo" | "patterns",
  data: SiteData,
  repo?: Repo,
): string {
  if (which === "index") return renderShell("memex 仓库目录", data, <IndexPage data={data} />);
  if (which === "patterns") return renderShell("跨仓模式", data, <PatternsPage data={data} />);
  if (repo === undefined) {
    // 显式失败，不静默出一张没有仓库内容的页
    throw new Error("renderPage(repo) 缺少 repo 参数");
  }
  return renderShell(repo.full_name, data, <RepoPage repo={repo} />);
}
