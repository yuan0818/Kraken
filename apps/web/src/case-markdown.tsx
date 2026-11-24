import { useMemo, useState, type ComponentProps } from "react";
import { Input, Segmented } from "antd";
import DOMPurify from "dompurify";
import { Marked } from "marked";

const escapeHtml = (text: string) =>
  text.replaceAll("&", "&amp;").replaceAll("<", "&lt;").replaceAll(">", "&gt;");

const markdown = new Marked({
  breaks: true,
  renderer: {
    html: ({ text }) => escapeHtml(text),
    image: ({ text }) => escapeHtml(text),
  },
});

const maxMarkdownLength = 64000;
const maxItems = 200;

function renderMarkdown(source: string) {
  return DOMPurify.sanitize(markdown.parse(source, { async: false }), {
    ALLOWED_TAGS: [
      "a",
      "blockquote",
      "br",
      "code",
      "del",
      "em",
      "h1",
      "h2",
      "h3",
      "h4",
      "h5",
      "h6",
      "hr",
      "li",
      "ol",
      "p",
      "pre",
      "strong",
      "table",
      "tbody",
      "td",
      "th",
      "thead",
      "tr",
      "ul",
    ],
    ALLOWED_ATTR: ["href", "start"],
    ALLOWED_URI_REGEXP: /^(https?:\/\/|mailto:)/i,
    ALLOW_DATA_ATTR: false,
    ALLOW_ARIA_ATTR: false,
  });
}

function isList(element: Element) {
  return element.tagName === "OL" || element.tagName === "UL";
}

function plainText(node: Node): string {
  if (!(node instanceof Element)) return node.textContent ?? "";
  if (node.tagName === "BR") return "\n";
  if (isList(node)) {
    const start = Number(node.getAttribute("start") ?? 1);
    const items = Array.from(node.children, (item, i) => {
      const prefix = node.tagName === "OL" ? `${start + i}. ` : "- ";
      return prefix + plainText(item).trim().replaceAll("\n", "\n  ");
    });
    return `\n${items.join("\n")}\n`;
  }
  if (node.tagName === "TR")
    return (
      Array.from(node.children, (cell) => plainText(cell).trim()).join(" | ") +
      "\n"
    );
  const text = Array.from(node.childNodes, plainText).join("");
  if (node.tagName === "A") {
    const href = node.getAttribute("href");
    return href && href !== text ? `${text} (${href})` : text;
  }
  return /^(P|PRE|H[1-6]|BLOCKQUOTE)$/.test(node.tagName) ? text + "\n" : text;
}

export function markdownItems(source: string) {
  const template = document.createElement("template");
  template.innerHTML = renderMarkdown(source);
  return Array.from(template.content.childNodes)
    .flatMap((node) => {
      if (!(node instanceof Element)) return plainText(node).split("\n");
      if (isList(node)) return Array.from(node.children, plainText);
      if (node.tagName === "P") return plainText(node).split("\n");
      return [plainText(node)];
    })
    .map((item) => item.trim())
    .filter(Boolean);
}

export function markdownFromItems(items: string[]) {
  return items
    .map((item, index) => {
      const prefix = `${index + 1}. `;
      const text = item
        .replace(/\r\n?/g, "\n")
        .split("\n")
        .map((line) =>
          line
            .replace(/[\\`*_[\]<>&~#|]/g, "\\$&")
            .replace(
              /^(\s*)([-+]|\d+[.)])(?=\s)/,
              (_, leading: string, marker: string) =>
                leading + marker.slice(0, -1) + "\\" + marker.slice(-1),
            ),
        )
        .join(`  \n${" ".repeat(prefix.length)}`);
      return prefix + text;
    })
    .join("\n");
}

export async function validateCaseMarkdown(
  _: unknown,
  source: string | undefined,
) {
  const count = markdownItems(source ?? "").length;
  if (!count) throw new Error("请填写至少一项内容");
  if (count > maxItems) throw new Error(`最多填写 ${maxItems} 项`);
}

export function MarkdownView({ source }: { source: string }) {
  const html = useMemo(() => renderMarkdown(source), [source]);
  return (
    <div className="case-markdown" dangerouslySetInnerHTML={{ __html: html }} />
  );
}

export function MarkdownInput({
  id,
  value = "",
  onChange,
  label,
  ...inputProps
}: Pick<
  ComponentProps<typeof Input.TextArea>,
  | "id"
  | "onBlur"
  | "placeholder"
  | "aria-describedby"
  | "aria-invalid"
  | "aria-required"
> & {
  value?: string;
  onChange?: (value: string) => void;
  label: string;
}) {
  const [mode, setMode] = useState("edit");
  return (
    <div className="markdown-input">
      <div className="markdown-input-toolbar">
        <span>Markdown</span>
        <Segmented
          size="small"
          aria-label={`${label}显示模式`}
          value={mode}
          onChange={setMode}
          options={[
            { label: "编辑", value: "edit" },
            { label: "预览", value: "preview" },
          ]}
        />
      </div>
      {mode === "edit" ? (
        <Input.TextArea
          {...inputProps}
          id={id}
          value={value}
          onChange={(event) => onChange?.(event.target.value)}
          autoSize={{ minRows: 5, maxRows: 15 }}
          maxLength={maxMarkdownLength}
        />
      ) : (
        <div className="markdown-input-preview" id={id} tabIndex={-1}>
          {value.trim() ? (
            <MarkdownView source={value} />
          ) : (
            <span className="muted">暂无内容</span>
          )}
        </div>
      )}
    </div>
  );
}
