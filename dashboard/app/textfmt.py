"""Small, safe Markdown for Claude's chat notes: **bold**, *italic*, `code`, ``` blocks, lists, # headings, paragraphs.
The text is HTML-escaped FIRST, then only these few tags are added — Claude's text can never inject HTML or scripts."""
import re

from markupsafe import Markup, escape

_CODE = re.compile(r"`([^`\n]+)`")
_BOLD = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_ITALIC = re.compile(r"(?<![*\w])\*(?=\S)([^*\n]+?)(?<=\S)\*(?![*\w])")
_UL = re.compile(r"^\s*[-*•]\s+(.*)$")
_OL = re.compile(r"^\s*\d+[.)]\s+(.*)$")
_HEAD = re.compile(r"^\s*#{1,6}\s+(.*)$")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")


def _inline(s):
    parts = _CODE.split(s)                       # odd items are code — no bold / italic inside them
    for i, part in enumerate(parts):
        if i % 2:
            parts[i] = f"<code>{part}</code>"
        else:
            parts[i] = _ITALIC.sub(r"<em>\1</em>", _BOLD.sub(r"<strong>\1</strong>", part))
    return "".join(parts)


def simple_md(text):
    lines = str(escape(text or "")).replace("\r\n", "\n").split("\n")
    out, para, items, kind, fence = [], [], [], None, None

    def flush():
        nonlocal para, items, kind
        if para:
            out.append("<p>" + "<br>".join(_inline(l) for l in para) + "</p>")
            para = []
        if items:
            out.append(f"<{kind}>" + "".join(f"<li>{_inline(i)}</li>" for i in items) + f"</{kind}>")
            items, kind = [], None

    for line in lines:
        if line.strip().startswith("```"):
            if fence is None:
                flush()
                fence = []
            else:
                out.append("<pre><code>" + "\n".join(fence) + "</code></pre>")
                fence = None
            continue
        if fence is not None:
            fence.append(line)
            continue
        if not line.strip() or _RULE.match(line):
            flush()
            continue
        m = _HEAD.match(line)
        if m:
            flush()
            out.append(f"<p><strong>{_inline(m.group(1))}</strong></p>")
            continue
        for rx, k in ((_UL, "ul"), (_OL, "ol")):
            m = rx.match(line)
            if m:
                if para or (kind and kind != k):
                    flush()
                kind = k
                items.append(m.group(1))
                break
        else:
            if items:
                flush()
            para.append(line.strip())
    if fence is not None:
        out.append("<pre><code>" + "\n".join(fence) + "</code></pre>")
    flush()
    return Markup("".join(out))
