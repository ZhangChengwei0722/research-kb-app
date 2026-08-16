import {
  Activity,
  ArrowLeftRight,
  Bot,
  CircleHelp,
  FileClock,
  LayoutDashboard,
  LibraryBig,
  ListChecks,
  BookOpenText,
  MessageSquareText,
  Network,
  Search,
  Sparkles,
  Tags,
  Vault,
  type LucideIcon,
} from "lucide-react";

export type ViewId = "overview" | "discovery" | "processing" | "agent" | "library" | "reading" | "query" | "organization" | "tags" | "screening" | "questions" | "synthesis" | "obsidian" | "exchange" | "health";

type CatalogViewId = Exclude<ViewId, "overview" | "discovery" | "processing" | "agent" | "reading" | "query" | "organization" | "tags" | "screening" | "obsidian" | "exchange">;

export type CatalogView = {
  id: CatalogViewId;
  label: string;
  eyebrow: string;
  title: string;
  emptyLabel: string;
  icon: LucideIcon;
  kinds: readonly string[];
  kindLabels: Readonly<Record<string, string>>;
};

export const navigation: ReadonlyArray<{
  id: ViewId;
  label: string;
  icon: LucideIcon;
}> = [
  { id: "overview", label: "总览", icon: LayoutDashboard },
  { id: "discovery", label: "发现", icon: Search },
  { id: "processing", label: "处理", icon: FileClock },
  { id: "agent", label: "Agent", icon: Bot },
  { id: "library", label: "文献", icon: LibraryBig },
  { id: "reading", label: "阅读", icon: BookOpenText },
  { id: "query", label: "问答", icon: MessageSquareText },
  { id: "organization", label: "研究组织", icon: Network },
  { id: "tags", label: "标签", icon: Tags },
  { id: "screening", label: "问题筛选", icon: ListChecks },
  { id: "questions", label: "问题", icon: CircleHelp },
  { id: "synthesis", label: "科研综合", icon: Sparkles },
  { id: "obsidian", label: "Obsidian", icon: Vault },
  { id: "exchange", label: "交换", icon: ArrowLeftRight },
  { id: "health", label: "健康", icon: Activity },
];

export const catalogViews: Readonly<Record<CatalogViewId, CatalogView>> = {
  library: {
    id: "library",
    label: "文献库",
    eyebrow: "LIBRARY",
    title: "论文与阅读产物",
    emptyLabel: "当前筛选没有文献记录",
    icon: LibraryBig,
    kinds: ["paper", "paper_card_unit", "evidence", "review_memory", "review_unit"],
    kindLabels: {
      paper: "论文",
      paper_card_unit: "Paper Card Unit",
      evidence: "Evidence",
      review_memory: "Review Memory",
      review_unit: "Review Unit",
    },
  },
  questions: {
    id: "questions",
    label: "研究问题",
    eyebrow: "QUESTIONS",
    title: "Question Mapping",
    emptyLabel: "当前筛选没有研究问题",
    icon: CircleHelp,
    kinds: ["question"],
    kindLabels: { question: "研究问题" },
  },
  synthesis: {
    id: "synthesis",
    label: "Research Synthesis",
    eyebrow: "RESEARCH SYNTHESIS",
    title: "Research Synthesis",
    emptyLabel: "当前筛选没有综合候选",
    icon: Sparkles,
    kinds: ["synthesis", "review_angle", "insight", "cross_view"],
    kindLabels: {
      synthesis: "Synthesis",
      review_angle: "Review Angle",
      insight: "Insight",
      cross_view: "Cross-View",
    },
  },
  health: {
    id: "health",
    label: "系统健康",
    eyebrow: "HEALTH",
    title: "运行记录与 Guardian",
    emptyLabel: "当前筛选没有运行记录",
    icon: Activity,
    kinds: ["process_event", "guardian_report"],
    kindLabels: {
      process_event: "Process Event",
      guardian_report: "Guardian Report",
    },
  },
};

export function kindLabel(view: CatalogView, kind: string): string {
  return view.kindLabels[kind] ?? kind.replaceAll("_", " ");
}
