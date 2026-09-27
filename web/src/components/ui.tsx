import type { ReactNode } from "react";
import type { AxisKey, CardKind } from "../types";

/** 缺值统一用破折号：报告禁止 N/A、无、未知、待补充、TODO 这类占位符（AGENTS.md） */
export const DASH = "—";

export function dash(v: string | number | null | undefined): string {
  if (v === null || v === undefined) return DASH;
  const s = String(v).trim();
  return s === "" ? DASH : s;
}

/** 五轴标签与顺序以 src/memex/constants.py:22-28 为准，别在前端另排一套 */
export const AXES: ReadonlyArray<readonly [AxisKey, string]> = [
  ["runtime_control_flow", "运行 / 控制流"],
  ["data_flow", "数据流"],
  ["state_lifecycle", "状态与生命周期"],
  ["failure_recovery", "失败恢复"],
  ["concurrency_timing", "并发与时序"],
] as const;

export const AXIS_LABEL: Record<AxisKey, string> = Object.fromEntries(
  AXES.map(([k, v]) => [k, v]),
) as Record<AxisKey, string>;

export const KIND_LABEL: Record<CardKind, string> = {
  mechanism: "机制",
  snippet: "片段",
  skeleton: "骨架",
  gotcha: "陷阱",
  decision: "决策",
};

export function shortSha(sha: string | null | undefined): string {
  if (!sha) return DASH;
  return sha.slice(0, 10);
}

/** 相似度只显示三位小数：全精度浮点会渲染成 0.6359999999999999 这类噪音 */
export function score3(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return DASH;
  return v.toFixed(3);
}

export function evidenceLine(
  path: string,
  start: number | undefined,
  end: number | undefined,
): string {
  if (start !== undefined && start !== null) {
    if (end !== undefined && end !== null && end !== start) {
      return `${path}:${start}-${end}`;
    }
    return `${path}:${start}`;
  }
  return path;
}

export function Card({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={"card" + (className ? " " + className : "")}>{children}</div>;
}

export function Badge({
  kind,
  children,
  tone,
}: {
  kind?: CardKind;
  children: ReactNode;
  tone?: string;
}) {
  const cls = tone ? "badge " + tone : kind ? "badge kind-" + kind : "badge";
  return <span className={cls}>{children}</span>;
}

export function Section({
  title,
  count,
  id,
  children,
}: {
  title: string;
  count?: number;
  id?: string;
  children: ReactNode;
}) {
  return (
    <section className="section" id={id}>
      <h2>
        {title}
        {count !== undefined ? <span className="count">{count}</span> : null}
      </h2>
      {children}
    </section>
  );
}
