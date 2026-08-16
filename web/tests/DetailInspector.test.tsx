import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { CatalogDetail, CatalogItem, JsonValue } from "../src/api";
import { catalogViews } from "../src/catalogViews";
import { DetailInspector } from "../src/components/DetailInspector";

type DetailCase = {
  name: string;
  view: typeof catalogViews.library;
  item: CatalogItem;
  detail: Record<string, JsonValue>;
  expected: string[];
};

const detailCases: DetailCase[] = [
  {
    name: "Evidence provenance",
    view: catalogViews.library,
    item: item("evidence", []),
    detail: {
      claim: "Synthetic claim",
      quote: "Synthetic quote",
      locator: "page:1:block:1",
      source_page: { pdf_page: 1, section: "Results" },
    },
    expected: ["Synthetic claim", "Synthetic quote", "Pdf page", "page:1:block:1"],
  },
  {
    name: "Review Unit background boundary",
    view: catalogViews.library,
    item: item("review_unit", ["not_fact"]),
    detail: {
      background_only: true,
      can_enter_canonical_evidence: false,
      unit: { content: "Synthetic reusable background", pdf_page: 2 },
    },
    expected: ["Background only", "Synthetic reusable background", "Can enter canonical evidence"],
  },
  {
    name: "Question mapping",
    view: catalogViews.questions,
    item: item("question", []),
    detail: {
      question_text: "Synthetic Question?",
      mapping_status: "ai_checked",
      paper_links: [{ paper_id: "paper_1234", role_in_question: "comparison" }],
    },
    expected: ["Synthetic Question?", "Mapping status", "comparison"],
  },
  {
    name: "Research Synthesis non-fact boundary",
    view: catalogViews.synthesis,
    item: item("synthesis", ["not_fact"]),
    detail: {
      claim: "Synthetic synthesis candidate",
      not_fact: true,
      evidence_base: ["evidence_1234"],
    },
    expected: ["Not a factual record", "Synthetic synthesis candidate", "evidence_1234"],
  },
];

describe("artifact detail inspector", () => {
  it.each(detailCases)("renders $name as escaped structured data", ({ view, item: selected, detail, expected }) => {
    render(
      <DetailInspector
        view={view}
        detail={response(selected, "current", detail)}
        loading={false}
        error=""
        onClose={() => undefined}
      />,
    );

    for (const value of expected) expect(screen.getAllByText(value, { exact: true }).length).toBeGreaterThan(0);
    expect(document.querySelector("script")).not.toBeInTheDocument();
  });

  it("does not render stale semantic content when the current record changed", () => {
    render(
      <DetailInspector
        view={catalogViews.library}
        detail={response(item("evidence", []), "changed", null)}
        loading={false}
        error=""
        onClose={() => undefined}
      />,
    );

    expect(screen.getByText("record:changed", { exact: true })).toBeInTheDocument();
    expect(screen.getByText(/无法按投影内容展示/)).toBeInTheDocument();
    expect(screen.queryByText("当前内容")).not.toBeInTheDocument();
  });
});

function item(itemKind: string, statusLabels: string[]): CatalogItem {
  return {
    item_id: "catalog_1234",
    item_kind: itemKind,
    authority_layer: "canonical",
    record_kind: itemKind,
    record_id: `${itemKind}_1234`,
    child_id: null,
    paper_id: "paper_1234",
    question_id: itemKind === "question" ? "question_1234" : null,
    title: `<unsafe ${itemKind}>`,
    summary: "Synthetic detail",
    status_labels: statusLabels,
    sort_key: itemKind,
  source_record_digest: "digest",
  adapter_version: "1.0",
  tags: [],
};
}

function response(
  selected: CatalogItem,
  currentRecordStatus: string,
  detail: Record<string, JsonValue> | null,
): CatalogDetail {
  return {
    status: "success",
    projection_state: "current",
    current_record_status: currentRecordStatus,
    item: selected,
    detail,
  };
}
