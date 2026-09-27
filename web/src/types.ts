/**
 * site-data.json 的类型契约（tech-design.md §2.7.1）。
 *
 * 这里的形状是**从 reports/report.schema.json 抄下来的**，不是另发明的：
 * report_* 字段原样内联，前端不做二次解释（decisions.md C11 改写二）。
 * 改这个文件前先改 docs，再改 Python 的 dump.py。
 */

/** report.schema.json 里的证据项 */
export interface ReportEvidence {
  path: string;
  start_line?: number;
  end_line?: number;
  symbol?: string;
  note?: string;
}

export interface CodeSpan {
  path: string;
  start_line: number;
  end_line: number;
}

export type CardKind = "mechanism" | "snippet" | "skeleton" | "gotcha" | "decision";

export interface ReportCard {
  kind: CardKind;
  title: string;
  summary: string;
  /** 契约强制英文 */
  mechanism_desc: string;
  reusable: boolean;
  evidence: ReportEvidence[];
  tags: string[];
  code?: string;
  /** 只有 snippet / skeleton 才有 */
  code_spans?: CodeSpan[];
}

export type AxisKey =
  | "runtime_control_flow"
  | "data_flow"
  | "state_lifecycle"
  | "failure_recovery"
  | "concurrency_timing";

export type Principles = Record<AxisKey, string>;

export interface ReportFeature {
  key: string;
  title: string;
  summary: string;
  /** 契约强制英文 */
  intent: string;
  principles: Principles;
  evidence: ReportEvidence[];
  cards: ReportCard[];
}

export interface Titled {
  title: string;
  detail: string;
  evidence: ReportEvidence[];
}

export interface EntryPoint {
  path: string;
  kind: string;
  role: string;
}

export interface Report {
  schema_id: string;
  one_liner: string;
  characteristics: Titled[];
  entry_points: EntryPoint[];
  features: ReportFeature[];
  cross_feature_risks: Titled[];
}

export interface Analysis {
  analysis_id: string | null;
  commit_sha: string;
  contract_version: string;
  depth: string | null;
  analyst: string;
  producer: string;
  status: string;
  created_at: string;
  finished_at: string | null;
  reindex_state: string | null;
  counts: Record<string, number>;
  quality: Record<string, unknown>;
  report: Report;
  report_md: string;
}

export interface Repo {
  repo_id: string;
  file: string;
  full_name: string;
  url: string;
  host: string;
  description: string | null;
  language: string | null;
  stars: number | null;
  license: string | null;
  is_stale: boolean;
  is_fork: boolean;
  fork_of: string | null;
  default_branch: string | null;
  source: string;
  analysis_count: number;
  latest: Analysis | null;
  analyses: Analysis[];
}

export interface PatternMember {
  card_id: string;
  score: number;
  kind: CardKind;
  title: string;
  summary: string;
  symbol: string | null;
  repo_id: string;
  repo_full_name: string | null;
  /** 脏 repo_id 指向的仓没被导出时为 null——不给链接比给死链诚实 */
  file: string | null;
  /** f{fi}-c{ci}，与 dump.py / analyze/rows.py 同一套下标；解不出时为 null */
  anchor: string | null;
}

export interface Pattern {
  pattern_id: string;
  key: string;
  title: string;
  tags: string[];
  card_count: number;
  repo_count: number;
  members: PatternMember[];
}

export interface SiteData {
  schema_id: string;
  generated_at: string;
  embedder: string | null;
  cluster_threshold: number;
  stats: {
    repos: number;
    analyses: number;
    cards: number;
    patterns: number;
  };
  repos: Repo[];
  patterns: Pattern[];
  skipped_repo_ids: string[];
}
