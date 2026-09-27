import type { SiteData } from "../types";
import { dash } from "../components/ui";

export function IndexPage({ data }: { data: SiteData }) {
  const s = data.stats;
  return (
    <main>
      <header>
        <h1>memex 仓库目录</h1>
        <p className="muted">
          已收录仓库清单（静态目录页，C11） · schema <code>{data.schema_id}</code>
        </p>
      </header>

      <div className="stats">
        <div className="stat">
          <div className="num">{s.repos}</div>
          <div className="lbl">仓库</div>
        </div>
        <div className="stat">
          <div className="num">{s.cards}</div>
          <div className="lbl">卡片</div>
        </div>
        <div className="stat">
          <div className="num">{s.patterns}</div>
          <div className="lbl">模式</div>
        </div>
        <div className="stat">
          <div className="num">{s.analyses}</div>
          <div className="lbl">分析</div>
        </div>
      </div>

      <nav className="links">
        <a href="patterns.html">跨仓模式（{s.patterns}）→</a>
      </nav>

      <table className="grid">
        <thead>
          <tr>
            <th>仓库</th>
            <th>语言</th>
            <th>Stars</th>
            <th>License</th>
            <th>分析</th>
          </tr>
        </thead>
        <tbody>
          {data.repos.map((r) => {
            const l = r.latest;
            const c = (l && l.counts) || {};
            return (
              <tr key={r.repo_id}>
                <td>
                  <a href={r.file}>{r.full_name}</a>
                  {r.description ? <div className="muted small">{r.description}</div> : null}
                </td>
                <td>{dash(r.language)}</td>
                <td>{dash(r.stars)}</td>
                <td>{dash(r.license)}</td>
                <td>
                  {l ? (
                    <>
                      <code>{(l.commit_sha || "").slice(0, 10)}</code>
                      <div className="muted small">
                        功能 {c.features ?? dash(c.features)} · 卡片 {c.cards ?? dash(c.cards)} · 证据{" "}
                        {c.evidence ?? dash(c.evidence)}
                      </div>
                    </>
                  ) : (
                    <span className="muted">未分析</span>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {data.skipped_repo_ids.length > 0 ? (
        <section className="section">
          <h2>被跳过的仓库</h2>
          <p className="muted">
            下面这些 repo_id 写不进安全文件名，已显式跳过（脏数据要暴露，不洗白）：
          </p>
          <ul>
            {data.skipped_repo_ids.map((s) => (
              <li key={s}>
                <code>{s}</code>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
    </main>
  );
}
