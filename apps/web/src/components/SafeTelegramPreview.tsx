import { createElement, type ReactNode } from "react";

import type { SourceLink } from "@stocks-agent/dashboard-contracts";

import { SafeSourceLink, safeHttpsUrl } from "./SafeSourceLink";

type PreviewTag = "strong" | "em" | "u" | "s" | "code" | "pre" | "a";
type PreviewNode = { kind: "text"; value: string } | {
  kind: "element";
  tag: PreviewTag;
  href: string | null;
  children: PreviewNode[];
};

function decodeEntities(value: string): string {
  const named: Record<string, string> = { amp: "&", lt: "<", gt: ">", quot: '"', "#39": "'" };
  return value.replace(/&(#\d{1,7}|#x[0-9a-f]{1,6}|amp|lt|gt|quot|#39);/gi, (match, entity: string) => {
    if (named[entity.toLowerCase()]) return named[entity.toLowerCase()];
    const radix = entity[1]?.toLowerCase() === "x" ? 16 : 10;
    const raw = radix === 16 ? entity.slice(2) : entity.slice(1);
    const codePoint = Number.parseInt(raw, radix);
    return Number.isSafeInteger(codePoint) && codePoint >= 0 && codePoint <= 0x10ffff
      ? String.fromCodePoint(codePoint)
      : match;
  });
}

function tokenizeTelegramMarkup(value: string): PreviewNode[] {
  const root: PreviewNode[] = [];
  const stack: Array<{ tag: "root" | PreviewTag; children: PreviewNode[] }> = [{ tag: "root", children: root }];
  const aliases: Record<string, PreviewTag> = {
    b: "strong", strong: "strong", i: "em", em: "em", u: "u", s: "s", strike: "s", del: "s", code: "code", pre: "pre", a: "a",
  };
  for (const token of value.match(/<[^>]*>|[^<]+|</g) ?? []) {
    const current = () => stack[stack.length - 1]?.children ?? root;
    if (/^<br\s*\/?\s*>$/i.test(token)) {
      current().push({ kind: "text", value: "\n" });
      continue;
    }
    const close = /^<\/([a-z]+)\s*>$/i.exec(token);
    if (close) {
      const tag = aliases[close[1]?.toLowerCase() ?? ""];
      if (tag && stack.length > 1 && stack[stack.length - 1]?.tag === tag) {
        stack.pop();
      } else {
        current().push({ kind: "text", value: token });
      }
      continue;
    }
    const open = /^<([a-z]+)(?:\s+href="([^"]*)")?\s*>$/i.exec(token);
    const tag = aliases[open?.[1]?.toLowerCase() ?? ""];
    if (open && tag) {
      if (tag === "a" && open[2] === undefined) {
        current().push({ kind: "text", value: token });
        continue;
      }
      const node: Extract<PreviewNode, { kind: "element" }> = {
        kind: "element",
        tag,
        href: tag === "a" ? safeHttpsUrl(decodeEntities(open[2] ?? "")) : null,
        children: [],
      };
      current().push(node);
      stack.push(node);
      continue;
    }
    current().push({ kind: "text", value: decodeEntities(token) });
  }
  return root;
}

function renderNodes(nodes: readonly PreviewNode[], prefix = "preview"): ReactNode[] {
  return nodes.map((node, index) => {
    const key = `${prefix}-${index}`;
    if (node.kind === "text") return node.value;
    const children = renderNodes(node.children, key);
    if (node.tag === "a" && !node.href) return createElement("span", { key }, children);
    if (node.tag === "a") return createElement("a", { href: node.href, key, rel: "noreferrer noopener", target: "_blank" }, children);
    return createElement(node.tag, { key }, children);
  });
}

export function SafeTelegramPreview({ text, links = [] }: { text: string; links?: SourceLink[] }) {
  return (
    <div className="telegram-preview">
      <div className="telegram-preview-content">{renderNodes(tokenizeTelegramMarkup(text))}</div>
      {links.length > 0 && (
        <ul className="source-list" aria-label="Sources">
          {links.map((source, index) => <li key={`${source.label}-${index}`}><SafeSourceLink source={source} /></li>)}
        </ul>
      )}
    </div>
  );
}
