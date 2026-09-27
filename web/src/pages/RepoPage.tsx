import type { Analysis, Repo } from "../types";
import { Badge, Card, dash, shortSha, AXIS_LABEL, KIND_LABEL } from "../components/ui";
import type { ReportCard, ReportFeature, ReportEvidence } from "../types";

function EvidenceList({ items }: { items: ReportEvidence[] }) {
  if (!items || items.length === 0) return null;
  return (
    <ul className="ev">
      {items.map((e, i) => (
        <li key={i}>
          <code>{e.path}</code>
          {e.start_line !== undefined ? (
            <span className="muted">
              {" "}
              :{e.start_line}
              {e.end_line !== undefined && e.end_line !== e.start_line
                ? "-" + e.end_line
                : ""}
            </span>
          ) : null}
          {e.symbol ? <span className="sym">{e.symbol}</span> : null}
          {e.note ? <span className="note">{e.note}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function CardBlock({ card, anchor }: { card: ReportCard; anchor: string }) {
  return (
    <Card>
      <div className="card-head" id={anchor}>
        <Badge kind={card.kind}>{KIND_LABEL[card.kind] ?? dash(card.kind)}</Badge>
        <h4>{card.title}</h4>
        {card.reusable ? <span className="reuse">可复用</span> : null}
      </div>
      <p className="sum">{card.summary}</p>
      <p className="en">
        <span className="en-label">mechanism_desc</span> {card.mechanism_desc}
      </p>
      {card.code_spans && card.code_spans.length > 0 ? (
        <ul className="ev">
          {card.code_spans.map((s, i) => (
            <li key={i}>
              <code>
                {s.path}:{s.start_line}
                {s.end_line !== s.start_line ? "-" + s.end_line : ""}
              </code>
            </li>
          ))}
        </ul>
      ) : null}
      {card.evidence && card.evidence.length > 0 ? (
        <EvidenceList items={card.evidence} />
      ) : null}
      {card.tags && card.tags.length > 0 ? (
        <p className="tags">
          {card.tags.map((t) => (
            <span className="tag" key={t}>
              {t}
            </span>
          ))}
        </p>
      ) : null}
    </Card>
  );
}

function FeatureBlock({ feature, index }: { feature: ReportFeature; index: number }) {
  return (
    <Card className="feature-block">
      <div className="card-head">
        <h3>{feature.title}</h3>
        <code className="key">{feature.key}</code>
      </div>
      <p className="sum">{feature.summary}</p>
      <p className="intent">
        <span className="muted">意图</span> {feature.intent}
      </p>
      <dl className="axes">
        {Object.entries(feature.principles).map(([k, v]) => (
          <div key={k}>
            <dt>{AXIS_LABEL[k as keyof typeof AXIS_LABEL] ?? k}</dt>
            <dd>{v}</dd>
          </div>
        ))}
      </dl>
      <div className="cards">
        {feature.cards.map((c, ci) => (
          <CardBlock
            key={ci}
            card={c}
            anchor={`f${index}-c${ci}`}
          />
        ))}
      </div>
    </Card>
  );
}

function AnalysisBlock({ a }: { a: Analysis }) {
  const r = a.report || ({} as Analysis["report"]);
  const feats = r.features || [];
  return (
    <div className="analysis">
      <div className="analysis-head">
        <code className="sha">{shortSha(a.commit_sha)}</code>
        <span className="muted">{a.created_at}</span>
        <Badge tone="kind-status">{a.status}</Badge>
        <span className="muted">分析者 {dash(a.analyst)}</span>
        <span className="muted">契约 {dash(a.contract_version)}</span>
      </div>
      <p className="lead">{r.one_liner}</p>

      {r.characteristics && r.characteristics.length > 0 ? (
        <div className="block">
          <h3>项目特征</h3>
          {r.characteristics.map((c, i) => (
            <div className="titled" key={i}>
              <strong>{c.title}</strong>
              <p>{c.detail}</p>
              <EvidenceList items={c.evidence} />
            </div>
          ))}
        </div>
      ) : null}

      {r.entry_points && r.entry_points.length > 0 ? (
        <div className="block">
          <h3>入口点</h3>
          <ul className="eps">
            {r.entry_points.map((e, i) => (
              <li key={i}>
                <code>{e.path}</code>
                <span className="muted"> {dash(e.kind)} </span>
                <span>{e.role}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}

      <div className="block">
        <h3>功能与原理轴</h3>
        {feats.map((f, i) => (
          <FeatureBlock key={i} feature={f} index={i} />
        ))}
      </div>

      {r.cross_feature_risks && r.cross_feature_risks.length > 0 ? (
        <div className="block">
          <h3>跨功能风险</h3>
          {r.cross_feature_risks.map((c, i) => (
            <div className="titled" key={i}>
              <strong>{c.title}</strong>
              <p>{c.detail}</p>
              <EvidenceList items={c.evidence} />
            </div>
          ))}
        </div>
      ) : null}

      <details className="raw">
        <summary>report_md 原文快照（{(a.report_md || "").length} 字符）</summary>
        <pre className="md">{a.report_md}</pre>
      </details>
    </div>
  );
}

export function RepoPage({ repo }: { repo: Repo }) {
  return (
    <main>
      <p className="back">
        <a href="index.html">← 仓库目录</a> · <a href="patterns.html">跨仓模式</a>
      </p>
      <header>
        <h1>{repo.full_name}</h1>
        <p className="muted">
          <a href={repo.url}>{repo.url}</a>
        </p>
        <p className="desc">{dash(repo.description)}</p>
        <ul className="meta">
          <li>语言 {dash(repo.language)}</li>
          <li>Stars {dash(repo.stars)}</li>
          <li>License {dash(repo.license)}</li>
          <li>托管 {dash(repo.host)}</li>
          <li>默认分支 {dash(repo.default_branch)}</li>
          <li>来源 {dash(repo.source)}</li>
        </ul>
      </header>

      <section className="section">
        <h2>
          分析<span className="count">{repo.analyses.length}</span>
        </h2>
        {repo.analyses.length === 0 ? (
          <p className="muted">尚未分析：该仓还没有落库的分析。</p>
        ) : (
          repo.analyses.map((a) => <AnalysisBlock key={a.analysis_id ?? a.commit_sha} a={a} />)
        )}
      </section>
    </main>
  );
}
