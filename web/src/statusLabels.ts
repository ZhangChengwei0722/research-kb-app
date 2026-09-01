/**
 * User-facing labels for Core status enumerations.
 *
 * Core speaks snake_case English on the wire. Rendering those values straight
 * into a Chinese product surface leaked strings like "source:available" and
 * "process:true" to end users; this table keeps the wire contract untouched
 * while giving every surface one place to localise from.
 */
const STATUS_LABELS: Readonly<Record<string, string>> = {
  // source availability
  available: "可用",
  missing: "缺失",
  relink_required: "需重新关联",
  // freshness / currentness / revision
  current: "最新",
  stale: "已过期",
  stale_source: "来源已变更",
  superseded: "已被取代",
  active: "生效中",
  none: "无",
  unknown: "未知",
  // parse binding
  bound: "已绑定",
  unbound: "未绑定",
  historical_not_materialized: "历史版本未落地",
  failed: "失败",
  // source adequacy
  sufficient: "充分",
  insufficient: "不充分",
  not_assessed: "未评估",
  // grounding
  grounded: "已闭合证据",
  ungrounded: "未闭合证据",
  needs_resolution: "待人工处理",
  passed_auto_checks: "已通过自动检查",
  // review / screening
  ai_checked: "AI 已检查",
  human_checked: "人工已确认",
  candidate: "候选",
  // operation / projection state
  idle: "空闲",
  ready: "就绪",
  running: "进行中",
  building: "重建中",
  queued: "排队中",
  cancelled: "已取消",
  complete: "已完成",
};

export function statusLabel(value: string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "未知";
  return STATUS_LABELS[value] ?? value.replaceAll("_", " ");
}

/** Renders a boolean capability fact as a readable state rather than "true"/"false". */
export function booleanLabel(value: boolean, trueLabel = "正常", falseLabel = "异常"): string {
  return value ? trueLabel : falseLabel;
}
