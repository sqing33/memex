import type { Pattern, SiteData } from "../types";
import { Badge, dash, score3 } from "../components/ui";

function PatternBlock({ p }: { p: Pattern }) {
  return (
    <section className="section" id={p.pattern_id}>
      <h2>
        {p.title}
        <span className="count">
          {p.repo_count} 仓 · {p.card_count} 卡
        </span>
      </h2>
      <p className="muted small">
        key <code>{dash(p.key)}</code>
        {p.tags.length > 0 ? (
          <>
            {" · "}
            {p.tags.map((t) => (
              <span className="tag" key={t}>
                {t}
              </span>
            ))}
          </>
        ) : null}
      </p>

      <div className="cards">
        {p.members.map((m) => (
          <div className="card member" key={m.card_id}>
            <div className="card-head">
              <Badge kind={m.kind}>{m.kind}</Badge>
              <h4>{m.title}</h4>
              <span className="score">相似度 {score3(m.score)}</span>
            </div>
            <p className="sum">{m.summary}</p>
            <p className="muted small">
              来源{" "}
              {m.file && m.anchor ? (
                <a href={`${m.file}#${m.anchor}`}>
                  {dash(m.repo_full_name)}
                </a>
              ) : (
                <span>{dash(m.repo_full_name)}</span>
              )}
              {m.symbol ? (
                <>
                  {" · "}
                  <code>{m.symbol}</code>
                </>
              ) : null}
            </p>
            {m.file && m.anchor ? null : (
              <p className="muted small">
                这张卡所在的仓没被导出（脏 repo_id 会被跳过），所以不给链接。
              </p>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}

export function PatternsPage({ data }: { data: SiteData }) {
  return (
    <main>
      <a className="back" href="index.html">
        ← 仓库目录
      </a>
      <header>
        <h1>跨仓模式</h1>
        <p className="muted">
          成员来自不同仓库的同机制卡片，聚类阈值 {score3(data.cluster_threshold)}
          （complete-linkage）。这里只读 pattern_members 里已落库的分值，不在页面现场重算相似度。
        </p>
      </header>

      {data.patterns.length === 0 ? (
        <p className="muted">
          还没有聚出跨仓模式（至少需要两个仓库各贡献一张卡片）。
        </p>
      ) : (
        data.patterns.map((p) => <PatternBlock key={p.pattern_id} p={p} />)
      )}
    </main>
  );
}
