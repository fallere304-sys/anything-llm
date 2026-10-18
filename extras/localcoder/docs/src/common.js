// 3 つの資料で共通の部品 (A4・游ゴシック・表・囲み・箇条書き)
const d = require("docx");
const {
  Document, Paragraph, TextRun, Table, TableRow, TableCell, WidthType, ShadingType, AlignmentType,
  HeadingLevel, LevelFormat, BorderStyle, Footer, Header, PageNumber, VerticalAlign, TableLayoutType,
} = d;

const FONT = { ascii: "Yu Gothic", eastAsia: "Yu Gothic", hAnsi: "Yu Gothic", cs: "Yu Gothic" };
const C = { navy: "1F3A5F", teal: "0B7A5C", light: "EEF4F8", pale: "F7FAFC", gray: "5F6B7A", line: "C9D3DD",
  warn: "FFF6E5", warnLine: "E0A030", good: "E9F6EF", bad: "FBECEC" };
const PAGE_W = 11906, PAGE_H = 16838, MARGIN = 1134, TEXT_W = PAGE_W - 2 * MARGIN;

function runs(text, opt = {}) {
  // **太字** を太字に
  const parts = String(text).split(/(\*\*[^*]+\*\*)/).filter(Boolean);
  return parts.map((p) => p.startsWith("**")
    ? new TextRun({ text: p.slice(2, -2), bold: true, ...opt })
    : new TextRun({ text: p, ...opt }));
}
const P = (text, opt = {}) => new Paragraph({ children: runs(text, opt.run || {}), spacing: { after: 120, line: 320 },
  alignment: opt.align, ...(opt.para || {}) });
const H1 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_1, children: [new TextRun(text)] });
const H2 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_2, children: [new TextRun(text)] });
const H3 = (text) => new Paragraph({ heading: HeadingLevel.HEADING_3, children: [new TextRun(text)] });
const B = (text, level = 0) => new Paragraph({ numbering: { reference: "bul", level }, children: runs(text),
  spacing: { after: 60, line: 300 } });
const N = (text, ref = "num", keep = false) => new Paragraph({ numbering: { reference: ref, level: 0 },
  children: runs(text), spacing: { after: 60, line: 300 }, keepNext: keep, keepLines: true });
const Space = (pt = 6) => new Paragraph({ children: [], spacing: { after: pt * 20 } });

const border = (color = C.line) => ({ style: BorderStyle.SINGLE, size: 4, color });
const borders = (color) => ({ top: border(color), bottom: border(color), left: border(color), right: border(color) });

function cell(content, width, opt = {}) {
  const paras = (Array.isArray(content) ? content : [content]).map((t) =>
    t instanceof Paragraph ? t : new Paragraph({ children: runs(t, opt.run || {}), spacing: { after: 40, line: 280 },
      alignment: opt.align }));
  return new TableCell({
    width: { size: width, type: WidthType.DXA }, children: paras, verticalAlign: opt.valign || VerticalAlign.TOP,
    shading: opt.fill ? { type: ShadingType.CLEAR, color: "auto", fill: opt.fill } : undefined,
    margins: { top: 80, bottom: 80, left: 120, right: 120 }, borders: borders(opt.line),
    columnSpan: opt.span,
  });
}

// 表: headers は null 可。widths は合計 TEXT_W (比で渡してもよい)
function table(headers, rows, ratio, opt = {}) {
  const sum = ratio.reduce((a, b) => a + b, 0);
  const total = opt.width || TEXT_W;
  const widths = ratio.map((r) => Math.floor((r / sum) * total));
  widths[widths.length - 1] += total - widths.reduce((a, b) => a + b, 0);
  const out = [];
  if (headers) {
    out.push(new TableRow({ tableHeader: true, cantSplit: true, children: headers.map((h, i) =>
      cell(h, widths[i], { fill: opt.headFill || C.navy, run: { bold: true, color: "FFFFFF" } })) }));
  }
  rows.forEach((r, ri) => out.push(new TableRow({ cantSplit: true, children: r.map((c, i) => cell(c, widths[i], {
    fill: (opt.firstColFill && i === 0) ? opt.firstColFill : (opt.zebra && ri % 2 ? C.pale : undefined),
    run: (opt.firstColBold && i === 0) ? { bold: true } : {},
  })) })));
  return new Table({ width: { size: total, type: WidthType.DXA }, columnWidths: widths, rows: out,
    layout: TableLayoutType.FIXED });
}

// 囲み (注意・要点)
function box(title, lines, fill = C.light, line = C.navy) {
  const content = [];
  if (title) content.push(new Paragraph({ children: [new TextRun({ text: title, bold: true, color: line })],
    spacing: { after: 80 } }));
  lines.forEach((l) => content.push(l instanceof Paragraph ? l
    : new Paragraph({ children: runs(l), spacing: { after: 60, line: 300 } })));
  return new Table({ width: { size: TEXT_W, type: WidthType.DXA }, columnWidths: [TEXT_W], layout: TableLayoutType.FIXED,
    rows: [new TableRow({ cantSplit: true, children: [new TableCell({ width: { size: TEXT_W, type: WidthType.DXA }, children: content,
      shading: { type: ShadingType.CLEAR, color: "auto", fill },
      margins: { top: 140, bottom: 140, left: 220, right: 220 },
      borders: { top: border(fill), bottom: border(fill), right: border(fill),
        left: { style: BorderStyle.SINGLE, size: 24, color: line } } })] })] });
}

function doc(title, children, opt = {}) {
  return new Document({
    creator: "LocalCoder", title, description: title,
    styles: {
      default: { document: { run: { font: FONT, size: 21 } } },
      paragraphStyles: [
        { id: "Heading1", name: "Heading 1", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { font: FONT, size: 32, bold: true, color: C.navy },
          paragraph: { spacing: { before: 360, after: 160 }, outlineLevel: 0, keepNext: true, keepLines: true,
            border: { bottom: { style: BorderStyle.SINGLE, size: 8, color: C.navy, space: 4 } } } },
        { id: "Heading2", name: "Heading 2", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { font: FONT, size: 26, bold: true, color: C.teal },
          paragraph: { spacing: { before: 280, after: 120 }, outlineLevel: 1, keepNext: true, keepLines: true } },
        { id: "Heading3", name: "Heading 3", basedOn: "Normal", next: "Normal", quickFormat: true,
          run: { font: FONT, size: 22, bold: true, color: C.navy },
          paragraph: { spacing: { before: 200, after: 80 }, outlineLevel: 2, keepNext: true, keepLines: true } },
      ],
    },
    numbering: { config: [
      { reference: "bul", levels: [
        { level: 0, format: LevelFormat.BULLET, text: "●", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 420, hanging: 280 } }, run: { color: C.teal, size: 16 } } },
        { level: 1, format: LevelFormat.BULLET, text: "－", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 840, hanging: 280 } } } } ] },
      ...["num", "num2", "num3", "num4", "num5", "num6"].map((reference) => ({ reference, levels: [
        { level: 0, format: LevelFormat.DECIMAL, text: "%1.", alignment: AlignmentType.LEFT,
          style: { paragraph: { indent: { left: 420, hanging: 320 } } } } ] })),
    ] },
    sections: [{
      properties: { page: { size: { width: PAGE_W, height: PAGE_H },
        margin: { top: MARGIN, bottom: MARGIN, left: MARGIN, right: MARGIN, header: 500, footer: 500 } } },
      headers: { default: new Header({ children: [new Paragraph({ alignment: AlignmentType.RIGHT,
        children: [new TextRun({ text: opt.header || title, size: 16, color: C.gray })] })] }) },
      footers: { default: new Footer({ children: [new Paragraph({ alignment: AlignmentType.CENTER,
        children: [new TextRun({ children: [PageNumber.CURRENT, " / ", PageNumber.TOTAL_PAGES], size: 16,
          color: C.gray })] })] }) },
      children,
    }],
  });
}

// 表紙・題字
function titleBlock(kicker, title, sub, meta) {
  return [
    new Paragraph({ children: [new TextRun({ text: kicker, color: C.teal, bold: true, size: 22 })], spacing: { after: 80 } }),
    new Paragraph({ children: [new TextRun({ text: title, color: C.navy, bold: true, size: 52 })], spacing: { after: 120 } }),
    new Paragraph({ children: [new TextRun({ text: sub, color: C.gray, size: 26 })], spacing: { after: 160 },
      border: { bottom: { style: BorderStyle.SINGLE, size: 12, color: C.teal, space: 8 } } }),
    ...(meta ? [new Paragraph({ children: [new TextRun({ text: meta, color: C.gray, size: 18 })], spacing: { after: 240 } })] : []),
  ];
}

module.exports = { d, C, TEXT_W, P, H1, H2, H3, B, N, Space, table, box, doc, titleBlock, runs, cell };
