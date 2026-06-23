"""Deterministic GFM markdown → HTML renderer (Phase 292), stdlib only.

A faithful port of the F# reference renderer
(``Fuaran.UI.Renderer.Markdown.toHtml`` in ``Fuaran.UI.Renderer.Core``): the
output is byte-identical to the F# and TypeScript hosts, verified against the
shared corpus at ``../wire-format-fixtures/markdown/corpus.json``.

Targets the GFM spec (github.github.com/gfm) at the common-case bar. The three
buckets (see ``../fuaran/docs/MARKDOWN.md``):

* IN  — CommonMark core + GFM tables / strikethrough / task lists / bare-URL
        autolinks.
* OUT — raw/inline HTML is escaped (no passthrough); math + Mermaid live as a
        ``Custom`` node / client-only pass (they would break byte-parity).
* DEFERRED — emoji / footnotes / heading anchors / sub-sup / definition lists /
        the full named-entity table render escaped-literal until added.

Escapes by construction (no raw-HTML passthrough; URLs via
``sanitize_url_or_blank``); the result still passes through
``sanitize_markdown_html`` as defence in depth.
"""

from __future__ import annotations

from .sanitize import sanitize_markdown_html, sanitize_url_or_blank

# ── Fable/host-parity primitives ─────────────────────────────────────────────
# Explicit whitespace / digit classes (NOT str.isspace / str.isdigit) so F#,
# TS, and Python classify identically — their Unicode sets differ at the edges,
# which would be a parity hazard. This fixed ASCII set is the cross-host contract.


def _is_ws(c: str) -> bool:
    # space (32) + tab/LF/VT/FF/CR (9-13) — the cross-host whitespace set.
    n = ord(c)
    return c == " " or (9 <= n <= 13)


def _is_digit(c: str) -> bool:
    return "0" <= c <= "9"


def _is_ascii_punct(c: str) -> bool:
    return ("!" <= c <= "/") or (":" <= c <= "@") or ("[" <= c <= "`") or ("{" <= c <= "~")


# Match the F# escape set exactly: & < > " (and NOT ').
def _escape_html(s: str) -> str:
    if not s:
        return ""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


# ── Entity decoding (common subset; the rest is DEFERRED) ────────────────────

_NAMED_ENTITIES = {
    "amp": "&",
    "lt": "<",
    "gt": ">",
    "quot": '"',
    "apos": "'",
    "nbsp": " ",
    "copy": "©",
    "reg": "®",
    "trade": "™",
    "hellip": "…",
    "mdash": "—",
    "ndash": "–",
    "lsquo": "‘",
    "rsquo": "’",
    "ldquo": "“",
    "rdquo": "”",
    "deg": "°",
    "plusmn": "±",
    "times": "×",
    "divide": "÷",
    "frac12": "½",
    "frac14": "¼",
    "frac34": "¾",
    "sup2": "²",
    "sup3": "³",
    "middot": "·",
    "bull": "•",
    "dagger": "†",
    "euro": "€",
    "pound": "£",
    "cent": "¢",
    "yen": "¥",
    "sect": "§",
    "para": "¶",
}


def _try_decode_entity(text: str, i: int) -> tuple[str, int] | None:
    """Decode an entity at ``text[i]`` (``&``). Returns ``(chars, next)`` or None."""
    semi = text.find(";", i)
    if semi < 0 or semi == i + 1:
        return None
    body = text[i + 1 : semi]
    if body.startswith("#"):
        is_hex = len(body) > 1 and body[1] in "xX"
        digits = body[2:] if is_hex else body[1:]
        if digits == "":
            return None
        try:
            code = int(digits, 16 if is_hex else 10)
        except ValueError:
            return None
        cp = 0xFFFD if code == 0 or code > 0x10FFFF else code
        return (chr(cp), semi + 1)
    s = _NAMED_ENTITIES.get(body)
    return (s, semi + 1) if s is not None else None


# ── Inline AST ───────────────────────────────────────────────────────────────
# Tagged tuples: ("text", s) ("raw", s) ("emph", [..]) ("strong", [..])
# ("strike", [..]) ("soft",) ("hard",)


def _norm_label(s: str) -> str:
    """Trim, collapse internal whitespace, lowercase (CommonMark label match)."""
    out: list[str] = []
    in_ws = False
    for ch in s.strip():
        if _is_ws(ch):
            in_ws = True
        else:
            if in_ws:
                out.append(" ")
            in_ws = False
            out.append(ch.lower())
    return "".join(out)


def _scan_code_span(text: str, i: int) -> tuple[tuple, int] | None:
    n = len(text)
    j = i
    while j < n and text[j] == "`":
        j += 1
    open_len = j - i
    k = j
    close_start = -1
    while k < n and close_start < 0:
        if text[k] == "`":
            m = k
            while m < n and text[m] == "`":
                m += 1
            if m - k == open_len:
                close_start = k
            k = m
        else:
            k += 1
    if close_start < 0:
        return None
    raw = text[j:close_start]
    collapsed = raw.replace("\r\n", " ").replace("\n", " ").replace("\r", " ")
    if len(collapsed) >= 2 and collapsed[0] == " " and collapsed[-1] == " " and collapsed.strip() != "":
        collapsed = collapsed[1:-1]
    return (("raw", "<code>" + _escape_html(collapsed) + "</code>"), close_start + open_len)


def _is_scheme_char(c: str) -> bool:
    return ("a" <= c <= "z") or ("A" <= c <= "Z") or ("0" <= c <= "9") or c in "+.-"


def _scan_autolink(text: str, i: int) -> tuple[tuple, int] | None:
    close = text.find(">", i)
    if close < 0:
        return None
    body = text[i + 1 : close]
    if body == "" or " " in body or "<" in body:
        return None
    colon = body.find(":")
    looks_uri = (
        2 <= colon <= 32
        and all(_is_scheme_char(ch) for ch in body[:colon])
        and (("a" <= body[0] <= "z") or ("A" <= body[0] <= "Z"))
    )
    looks_email = not looks_uri and "@" in body and ":" not in body and body.find("@") > 0
    if looks_uri:
        safe = sanitize_url_or_blank(body)
        return (("raw", '<a href="' + _escape_html(safe) + '">' + _escape_html(body) + "</a>"), close + 1)
    if looks_email:
        return (("raw", '<a href="mailto:' + _escape_html(body) + '">' + _escape_html(body) + "</a>"), close + 1)
    return None


def _scan_inline_destination(text: str, start: int) -> tuple[str, str | None, int] | None:
    n = len(text)
    i = start
    while i < n and text[i] in " \n\t":
        i += 1
    url = ""
    ok = True
    if i < n and text[i] == "<":
        close = text.find(">", i)
        if close < 0:
            ok = False
        else:
            url = text[i + 1 : close]
            i = close + 1
    else:
        depth = 0
        parts: list[str] = []
        go = True
        while go and i < n:
            c = text[i]
            if c in " \t\n":
                go = False
            elif c == "(":
                depth += 1
                parts.append(c)
                i += 1
            elif c == ")":
                if depth == 0:
                    go = False
                else:
                    depth -= 1
                    parts.append(c)
                    i += 1
            elif c == "\\" and i + 1 < n and _is_ascii_punct(text[i + 1]):
                parts.append(text[i + 1])
                i += 2
            else:
                parts.append(c)
                i += 1
        url = "".join(parts)
    title: str | None = None
    if ok:
        j = i
        while j < n and text[j] in " \t\n":
            j += 1
        if j < n and text[j] in "\"'":
            q = text[j]
            t_close = text.find(q, j + 1)
            if t_close >= 0:
                title = text[j + 1 : t_close]
                i = t_close + 1
                while i < n and text[i] in " \t\n":
                    i += 1
    if not ok:
        return None
    if i < n and text[i] == ")":
        return (url, title, i + 1)
    return None


def _match_bracket(text: str, open0: int) -> int:
    n = len(text)
    i = open0 + 1
    depth = 1
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            i += 2
        elif c == "`":
            cs = _scan_code_span(text, i)
            i = cs[1] if cs else i + 1
        elif c == "[":
            depth += 1
            i += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return i
            i += 1
        else:
            i += 1
    return -1


def _render_inlines(nodes: list) -> str:
    out: list[str] = []
    for node in nodes:
        tag = node[0]
        if tag == "text":
            out.append(_escape_html(node[1]))
        elif tag == "raw":
            out.append(node[1])
        elif tag == "emph":
            out.append("<em>" + _render_inlines(node[1]) + "</em>")
        elif tag == "strong":
            out.append("<strong>" + _render_inlines(node[1]) + "</strong>")
        elif tag == "strike":
            out.append("<del>" + _render_inlines(node[1]) + "</del>")
        elif tag == "soft":
            out.append("\n")
        elif tag == "hard":
            out.append("<br />\n")
    return "".join(out)


def _plain_text(nodes: list) -> str:
    out: list[str] = []
    for node in nodes:
        tag = node[0]
        if tag == "text":
            out.append(node[1])
        elif tag in ("emph", "strong", "strike"):
            out.append(_plain_text(node[1]))
        elif tag in ("soft", "hard"):
            out.append(" ")
    return "".join(out)


def _scan_bare_autolink(text: str, i: int) -> tuple[tuple, int] | None:
    n = len(text)

    def starts(p: str) -> bool:
        return text[i : i + len(p)] == p

    if starts("https://"):
        pass
    elif starts("http://"):
        pass
    elif starts("www."):
        pass
    else:
        return None
    j = i
    while j < n and not _is_ws(text[j]) and text[j] != "<":
        j += 1
    while j > i and text[j - 1] in ".,;:!?)\"'":
        j -= 1
    if j <= i + 4:
        return None
    raw = text[i:j]
    href = "http://" + raw if raw.startswith("www.") else raw
    safe = sanitize_url_or_blank(href)
    return (("raw", '<a href="' + _escape_html(safe) + '">' + _escape_html(raw) + "</a>"), j)


def _tokenize(refs: dict, text: str) -> list:
    toks: list = []
    n = len(text)
    i = 0
    pending: list[str] = []

    def flush() -> None:
        if pending:
            toks.append(("node", ("text", "".join(pending))))
            pending.clear()

    def prev_char() -> str:
        return " " if i == 0 else text[i - 1]

    def make_image(label_text: str, url: str, title_opt: str | None) -> None:
        flush()
        alt = _plain_text(_parse_inlines(refs, label_text))
        safe = sanitize_url_or_blank(url)
        title_attr = (' title="' + _escape_html(title_opt) + '"') if title_opt is not None else ""
        toks.append(
            (
                "node",
                ("raw", '<img src="' + _escape_html(safe) + '" alt="' + _escape_html(alt) + '"' + title_attr + " />"),
            )
        )

    def make_link(label_text: str, url: str, title_opt: str | None) -> None:
        flush()
        inner = _render_inlines(_parse_inlines(refs, label_text))
        safe = sanitize_url_or_blank(url)
        title_attr = (' title="' + _escape_html(title_opt) + '"') if title_opt is not None else ""
        toks.append(("node", ("raw", '<a href="' + _escape_html(safe) + '"' + title_attr + ">" + inner + "</a>")))

    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n and text[i + 1] == "\n":
            flush()
            toks.append(("node", ("hard",)))
            i += 2
        elif c == "\\" and i + 1 < n and _is_ascii_punct(text[i + 1]):
            pending.append(text[i + 1])
            i += 2
        elif c == "`":
            cs = _scan_code_span(text, i)
            if cs:
                flush()
                toks.append(("node", cs[0]))
                i = cs[1]
            else:
                pending.append(c)
                i += 1
        elif c == "&":
            ent = _try_decode_entity(text, i)
            if ent:
                pending.append(ent[0])
                i = ent[1]
            else:
                pending.append(c)
                i += 1
        elif c == "<":
            al = _scan_autolink(text, i)
            if al:
                flush()
                toks.append(("node", al[0]))
                i = al[1]
            else:
                pending.append(c)
                i += 1
        elif c == "!" and i + 1 < n and text[i + 1] == "[":
            close = _match_bracket(text, i + 1)
            if close == -1:
                pending.append(c)
                i += 1
            else:
                label_text = text[i + 2 : close]
                if close + 1 < n and text[close + 1] == "(":
                    dest = _scan_inline_destination(text, close + 2)
                    if dest:
                        make_image(label_text, dest[0], dest[1])
                        i = dest[2]
                    else:
                        pending.append(c)
                        i += 1
                else:
                    ref_label, consumed_to = label_text, close + 1
                    if close + 1 < n and text[close + 1] == "[":
                        r2 = _match_bracket(text, close + 1)
                        if r2 > 0:
                            inner = text[close + 2 : r2]
                            ref_label = label_text if inner.strip() == "" else inner
                            consumed_to = r2 + 1
                    found = refs.get(_norm_label(ref_label))
                    if found is not None:
                        make_image(label_text, found[0], found[1])
                        i = consumed_to
                    else:
                        pending.append(c)
                        i += 1
        elif c == "[":
            close = _match_bracket(text, i)
            if close == -1:
                pending.append(c)
                i += 1
            else:
                label_text = text[i + 1 : close]
                if close + 1 < n and text[close + 1] == "(":
                    dest = _scan_inline_destination(text, close + 2)
                    if dest:
                        make_link(label_text, dest[0], dest[1])
                        i = dest[2]
                    else:
                        pending.append(c)
                        i += 1
                else:
                    ref_label, consumed_to = label_text, close + 1
                    if close + 1 < n and text[close + 1] == "[":
                        r2 = _match_bracket(text, close + 1)
                        if r2 > 0:
                            inner = text[close + 2 : r2]
                            ref_label = label_text if inner.strip() == "" else inner
                            consumed_to = r2 + 1
                    found = refs.get(_norm_label(ref_label))
                    if found is not None:
                        make_link(label_text, found[0], found[1])
                        i = consumed_to
                    else:
                        pending.append(c)
                        i += 1
        elif c in "*_~":
            j = i
            while j < n and text[j] == c:
                j += 1
            run_len = j - i
            before = prev_char()
            after = text[j] if j < n else " "
            before_ws = _is_ws(before)
            after_ws = _is_ws(after)
            before_punct = _is_ascii_punct(before)
            after_punct = _is_ascii_punct(after)
            left_flank = (not after_ws) and ((not after_punct) or before_ws or before_punct)
            right_flank = (not before_ws) and ((not before_punct) or after_ws or after_punct)
            if c == "_":
                can_open = left_flank and ((not right_flank) or before_punct)
                can_close = right_flank and ((not left_flank) or after_punct)
            else:
                can_open = left_flank
                can_close = right_flank
            flush()
            if c == "~" and run_len != 2:
                toks.append(("node", ("text", c * run_len)))
            else:
                toks.append(
                    {
                        "kind": "delim",
                        "ch": c,
                        "count": run_len,
                        "canOpen": can_open,
                        "canClose": can_close,
                        "active": True,
                    }
                )
            i = j
        elif c == "\n":
            s = "".join(pending)
            trimmed_end = s.rstrip(" ")
            hard = len(s) - len(trimmed_end) >= 2
            pending.clear()
            pending.append(trimmed_end)
            flush()
            toks.append(("node", ("hard",) if hard else ("soft",)))
            i += 1
        elif c in "hw" and (i == 0 or _is_ws(prev_char()) or prev_char() in "(*_~"):
            ba = _scan_bare_autolink(text, i)
            if ba:
                flush()
                toks.append(("node", ba[0]))
                i = ba[1]
            else:
                pending.append(c)
                i += 1
        else:
            pending.append(c)
            i += 1

    flush()
    # Tag node tokens uniformly as tuples for the emphasis pass.
    return [({"kind": "node", "node": t[1]} if isinstance(t, tuple) else t) for t in toks]


def _process_emphasis(toks: list) -> list:
    closer_idx = 0
    while closer_idx < len(toks):
        closer = toks[closer_idx]
        if not (
            isinstance(closer, dict)
            and closer.get("kind") == "delim"
            and closer["canClose"]
            and closer["active"]
            and closer["count"] > 0
        ):
            closer_idx += 1
            continue
        opener_idx = closer_idx - 1
        found = -1
        while opener_idx >= 0 and found < 0:
            o = toks[opener_idx]
            if (
                isinstance(o, dict)
                and o.get("kind") == "delim"
                and o["ch"] == closer["ch"]
                and o["canOpen"]
                and o["active"]
                and o["count"] > 0
            ):
                if (o["canClose"] or closer["canOpen"]) and closer["ch"] != "~":
                    sum_ok = (o["count"] + closer["count"]) % 3 != 0 or (
                        o["count"] % 3 == 0 and closer["count"] % 3 == 0
                    )
                else:
                    sum_ok = True
                if sum_ok:
                    found = opener_idx
                else:
                    opener_idx -= 1
            else:
                opener_idx -= 1
        if found < 0:
            if not closer["canOpen"]:
                closer["active"] = False
                toks[closer_idx] = {"kind": "node", "node": ("text", closer["ch"] * closer["count"])}
            closer_idx += 1
            continue
        opener = toks[found]
        if closer["ch"] == "~":
            use_count = 2
        elif opener["count"] >= 2 and closer["count"] >= 2:
            use_count = 2
        else:
            use_count = 1
        inner: list = []
        for k in range(found + 1, closer_idx):
            tk = toks[k]
            if isinstance(tk, dict) and tk.get("kind") == "node":
                inner.append(tk["node"])
            elif isinstance(tk, dict) and tk.get("kind") == "delim" and tk["count"] > 0:
                inner.append(("text", tk["ch"] * tk["count"]))
        if closer["ch"] == "~":
            wrapped = ("strike", inner)
        elif use_count == 2:
            wrapped = ("strong", inner)
        else:
            wrapped = ("emph", inner)
        opener["count"] -= use_count
        closer["count"] -= use_count
        rebuilt: list = []
        rebuilt.extend(toks[:found])
        if opener["count"] > 0:
            rebuilt.append({"kind": "node", "node": ("text", opener["ch"] * opener["count"])})
        rebuilt.append({"kind": "node", "node": wrapped})
        if closer["count"] > 0:
            rebuilt.append({"kind": "node", "node": ("text", closer["ch"] * closer["count"])})
        rebuilt.extend(toks[closer_idx + 1 :])
        toks[:] = rebuilt
        closer_idx = found

    result: list = []
    for t in toks:
        if isinstance(t, dict) and t.get("kind") == "node":
            result.append(t["node"])
        elif isinstance(t, dict) and t.get("kind") == "delim" and t["count"] > 0:
            result.append(("text", t["ch"] * t["count"]))
    return result


def _parse_inlines(refs: dict, text: str) -> list:
    return _process_emphasis(_tokenize(refs, text))


def _render_inline(refs: dict, text: str) -> str:
    return _render_inlines(_parse_inlines(refs, text))


# ── Block parsing ─────────────────────────────────────────────────────────────


def _leading_indent(s: str) -> int:
    n = 0
    i = 0
    while i < len(s):
        if s[i] == " ":
            n += 1
            i += 1
        elif s[i] == "\t":
            n += 4 - (n % 4)
            i += 1
        else:
            break
    return n


def _is_blank(s: str) -> bool:
    return s.strip() == ""


def _is_thematic_break(line: str) -> bool:
    t = line.strip().replace(" ", "").replace("\t", "")
    return len(t) >= 3 and (all(c == "-" for c in t) or all(c == "*" for c in t) or all(c == "_" for c in t))


def _try_atx_heading(line: str) -> tuple[int, str] | None:
    if _leading_indent(line) >= 4:
        return None
    t = line.lstrip(" ")
    lvl = 0
    while lvl < len(t) and lvl < 7 and t[lvl] == "#":
        lvl += 1
    if lvl == 0 or lvl > 6:
        return None
    if lvl < len(t) and t[lvl] not in " \t":
        return None
    body = t[lvl:].strip()
    stripped = body.rstrip("#").rstrip(" \t")
    final = "" if body != "" and all(c == "#" for c in body) else stripped
    return (lvl, final)


def _parse_align_row(line: str) -> list[str] | None:
    trimmed = line.strip()
    if "-" not in trimmed:
        return None
    body = trimmed.strip("|")
    cells = [s.strip() for s in body.split("|")]
    if len(cells) == 0:
        return None
    aligns: list[str] = []
    for core in cells:
        if core == "":
            return None
        left = core.startswith(":")
        right = core.endswith(":")
        dashes = core.strip(":")
        if dashes == "" or not all(c == "-" for c in dashes):
            return None
        aligns.append("center" if (left and right) else "left" if left else "right" if right else "none")
    return aligns


def _split_table_row(line: str) -> list[str]:
    t = line.strip()
    body = t[1:] if t.startswith("|") else t
    body2 = body[:-1] if (body.endswith("|") and not body.endswith("\\|")) else body
    cells: list[str] = []
    buf: list[str] = []
    i = 0
    while i < len(body2):
        c = body2[i]
        if c == "\\" and i + 1 < len(body2) and body2[i + 1] == "|":
            buf.append("|")
            i += 2
        elif c == "|":
            cells.append("".join(buf).strip())
            buf = []
            i += 1
        else:
            buf.append(c)
            i += 1
    cells.append("".join(buf).strip())
    return cells


def _extract_ref_defs(lines: list[str]) -> tuple[dict, list[str]]:
    refs: dict = {}
    kept: list[str] = []
    for line in lines:
        t = line.strip()
        handled = False
        if t.startswith("[") and _leading_indent(line) < 4:
            close = t.find("]")
            if close > 1 and close + 1 < len(t) and t[close + 1] == ":":
                label = t[1:close]
                rest = t[close + 2 :].strip()
                if rest != "" and "]" not in label:
                    space_idx = _index_of_any(rest, " \t")
                    if space_idx < 0:
                        url, title_part = rest, ""
                    else:
                        url, title_part = rest[:space_idx], rest[space_idx:].strip()
                    url_clean = url[1:-1] if url.startswith("<") and url.endswith(">") else url
                    title = None
                    if len(title_part) >= 2 and title_part[0] in "\"'":
                        q = title_part[0]
                        tc = title_part.find(q, 1)
                        if tc > 0:
                            title = title_part[1:tc]
                    refs[_norm_label(label)] = (url_clean, title)
                    handled = True
        if not handled:
            kept.append(line)
    return refs, kept


def _index_of_any(s: str, chars: str) -> int:
    for i, ch in enumerate(s):
        if ch in chars:
            return i
    return -1


def _is_list_marker(s: str) -> bool:
    if s == "":
        return False
    if s[0] in "-*+" and len(s) >= 2 and s[1] in " \t":
        return True
    k = 0
    while k < len(s) and k < 9 and _is_digit(s[k]):
        k += 1
    return k > 0 and k + 1 < len(s) and s[k] in ".)" and s[k + 1] in " \t"


# Block tuples:
#  ("heading", level, text) ("paragraph", text) ("hr",)
#  ("fenced", lang, content) ("indented", content) ("blockquote", [blocks])
#  ("bullet", tight, [items]) ("ordered", start, tight, [items])
#  ("table", headers, aligns, rows)
# A list item is {"task": None|bool, "blocks": [blocks]}.


def _parse_blocks(lines: list[str]) -> list:
    blocks: list = []
    n = len(lines)
    i = 0
    while i < n:
        line = lines[i]
        if _is_blank(line):
            i += 1
            continue
        if _is_thematic_break(line) and _try_atx_heading(line) is None:
            blocks.append(("hr",))
            i += 1
            continue
        atx = _try_atx_heading(line)
        if atx is not None:
            blocks.append(("heading", atx[0], atx[1]))
            i += 1
            continue
        indent = _leading_indent(line)
        trimmed_start = line.lstrip(" \t")
        if trimmed_start.startswith("```") or trimmed_start.startswith("~~~"):
            fence = "```" if trimmed_start.startswith("```") else "~~~"
            info = trimmed_start[3:].strip()
            lang = "" if info == "" else _split_ws(info)[0]
            content: list[str] = []
            j = i + 1
            closed = False
            while j < n and not closed:
                ln = lines[j]
                if ln.lstrip(" \t").startswith(fence) and ln.strip().rstrip(fence[0]) == "":
                    closed = True
                    j += 1
                else:
                    content.append(ln)
                    j += 1
            blocks.append(("fenced", lang, "\n".join(content)))
            i = j
        elif indent >= 4:
            content = []
            j = i
            while j < n and (_leading_indent(lines[j]) >= 4 or _is_blank(lines[j])):
                ln = lines[j]
                content.append("" if _is_blank(ln) else ln[min(4, len(ln)) :])
                j += 1
            while content and content[-1] == "":
                content.pop()
            blocks.append(("indented", "\n".join(content)))
            i = j
        elif trimmed_start.startswith(">"):
            inner: list[str] = []
            j = i
            while j < n and lines[j].lstrip(" \t").startswith(">"):
                ln = lines[j].lstrip(" \t")[1:]
                inner.append(ln[1:] if ln.startswith(" ") else ln)
                j += 1
            blocks.append(("blockquote", _parse_blocks(inner)))
            i = j
        elif (
            "|" in line
            and i + 1 < n
            and _parse_align_row(lines[i + 1]) is not None
            and len(_parse_align_row(lines[i + 1])) == len(_split_table_row(line))
        ):
            headers = _split_table_row(line)
            aligns = _parse_align_row(lines[i + 1])
            rows: list[list[str]] = []
            j = i + 2
            while j < n and not _is_blank(lines[j]) and "|" in lines[j]:
                rows.append(_split_table_row(lines[j]))
                j += 1
            blocks.append(("table", headers, aligns, rows))
            i = j
        elif _is_list_marker(trimmed_start):
            list_block, nxt = _parse_list(lines, i)
            blocks.append(list_block)
            i = nxt
        else:
            para: list[str] = []
            j = i
            stop = False
            setext = 0
            while j < n and not stop:
                ln = lines[j]
                if _is_blank(ln):
                    stop = True
                elif (
                    j > i
                    and _leading_indent(ln) < 4
                    and ln.strip() != ""
                    and (all(c == "=" for c in ln.strip()) or all(c == "-" for c in ln.strip()))
                ):
                    setext = 1 if ln.strip()[0] == "=" else 2
                    stop = True
                    j += 1
                elif j > i and (
                    _is_thematic_break(ln)
                    or _try_atx_heading(ln) is not None
                    or ln.lstrip(" ").startswith(">")
                    or _is_list_marker(ln.lstrip(" \t"))
                ):
                    stop = True
                else:
                    para.append(ln.lstrip(" \t"))
                    j += 1
            text = "\n".join(para).rstrip(" \t\n")
            if setext > 0 and text != "":
                blocks.append(("heading", setext, text))
            elif text != "":
                blocks.append(("paragraph", text))
            i = j
    return blocks


def _split_ws(s: str) -> list[str]:
    """Split on the first run of space/tab (mirrors F# Split([' ','\\t'])[0] usage)."""
    idx = _index_of_any(s, " \t")
    return [s] if idx < 0 else [s[:idx], s[idx + 1 :]]


def _parse_list(lines: list[str], start: int):
    n = len(lines)
    first = lines[start].lstrip(" \t")
    ordered = _is_digit(first[0])
    start_num = 1
    if ordered:
        k = 0
        while k < len(first) and _is_digit(first[k]):
            k += 1
        try:
            start_num = int(first[:k])
        except ValueError:
            start_num = 1

    def marker_width(s: str) -> int:
        if not ordered:
            return 2
        k = 0
        while k < len(s) and _is_digit(s[k]):
            k += 1
        return k + 2

    items: list = []
    i = start
    tight = True
    saw_blank_between = False
    go = True
    while go and i < n:
        raw = lines[i]
        trimmed = raw.lstrip(" \t")
        base_indent = _leading_indent(raw)
        if _is_blank(raw):
            saw_blank_between = True
            i += 1
        elif _is_list_marker(trimmed) and (ordered == _is_digit(trimmed[0])) and base_indent < 4:
            if saw_blank_between and items:
                tight = False
            saw_blank_between = False
            content_offset = base_indent + marker_width(trimmed)
            mw = marker_width(trimmed)
            after_marker = trimmed[mw:] if len(trimmed) > mw else ""
            if after_marker.startswith("[ ]"):
                task: bool | None = False
                first_content = after_marker[3:].lstrip(" ")
            elif after_marker.startswith("[x]") or after_marker.startswith("[X]"):
                task = True
                first_content = after_marker[3:].lstrip(" ")
            else:
                task = None
                first_content = after_marker
            item_lines: list[str] = [first_content]
            i += 1
            in_item = True
            while in_item and i < n:
                ln = lines[i]
                if _is_blank(ln):
                    item_lines.append("")
                    i += 1
                elif _leading_indent(ln) >= content_offset:
                    item_lines.append(ln[min(content_offset, len(ln)) :])
                    i += 1
                elif _is_list_marker(ln.lstrip(" \t")) and _leading_indent(ln) < 4:
                    in_item = False
                elif _leading_indent(ln) > 0 and not _is_list_marker(ln.lstrip(" \t")):
                    item_lines.append(ln.strip())
                    i += 1
                else:
                    in_item = False
            while item_lines and item_lines[-1] == "":
                item_lines.pop()
                saw_blank_between = True
            if any(ln == "" for ln in item_lines):
                tight = False
            items.append({"task": task, "blocks": _parse_blocks(item_lines)})
        else:
            go = False

    block = ("ordered", start_num, tight, items) if ordered else ("bullet", tight, items)
    return block, i


# ── Block rendering ────────────────────────────────────────────────────────


def _align_attr(a: str) -> str:
    if a == "left":
        return ' align="left"'
    if a == "center":
        return ' align="center"'
    if a == "right":
        return ' align="right"'
    return ""


def _render_blocks(refs: dict, blocks: list) -> str:
    return "".join(_render_block(refs, b) for b in blocks)


def _render_block(refs: dict, b: tuple) -> str:
    tag = b[0]
    if tag == "hr":
        return "<hr />\n"
    if tag == "heading":
        lvl = b[1]
        return "<h" + str(lvl) + ">" + _render_inline(refs, b[2]) + "</h" + str(lvl) + ">\n"
    if tag == "paragraph":
        return "<p>" + _render_inline(refs, b[1]) + "</p>\n"
    if tag == "fenced":
        lang, content = b[1], b[2]
        cls = "" if lang == "" else ' class="language-' + _escape_html(lang) + '"'
        return "<pre><code" + cls + ">" + _escape_html(content) + "\n</code></pre>\n"
    if tag == "indented":
        return "<pre><code>" + _escape_html(b[1]) + "\n</code></pre>\n"
    if tag == "blockquote":
        return "<blockquote>\n" + _render_blocks(refs, b[1]) + "</blockquote>\n"
    if tag == "table":
        headers, aligns, rows = b[1], b[2], b[3]
        out: list[str] = ['<table class="fuaran-table"><thead><tr>']
        for idx, h in enumerate(headers):
            a = aligns[idx] if idx < len(aligns) else "none"
            out.append('<th class="fuaran-table-header"' + _align_attr(a) + ">" + _render_inline(refs, h) + "</th>")
        out.append("</tr></thead><tbody>")
        for row in rows:
            out.append('<tr class="fuaran-table-row">')
            for idx in range(len(headers)):
                cell = row[idx] if idx < len(row) else ""
                a = aligns[idx] if idx < len(aligns) else "none"
                out.append(
                    '<td class="fuaran-table-cell"' + _align_attr(a) + ">" + _render_inline(refs, cell) + "</td>"
                )
            out.append("</tr>")
        out.append("</tbody></table>\n")
        return "".join(out)
    if tag == "bullet":
        return "<ul>\n" + _render_items(refs, b[1], b[2]) + "</ul>\n"
    if tag == "ordered":
        start_num, tight, items = b[1], b[2], b[3]
        start_attr = "" if start_num == 1 else ' start="' + str(start_num) + '"'
        return "<ol" + start_attr + ">\n" + _render_items(refs, tight, items) + "</ol>\n"
    return ""


def _render_items(refs: dict, tight: bool, items: list) -> str:
    out: list[str] = []
    for item in items:
        task = item["task"]
        if task is None:
            checkbox = ""
        elif task is False:
            checkbox = '<input class="fuaran-task-checkbox" disabled="" type="checkbox" /> '
        else:
            checkbox = '<input class="fuaran-task-checkbox" checked="" disabled="" type="checkbox" /> '
        li_class = ' class="fuaran-task-item"' if task is not None else ""
        if tight:
            inner_parts: list[str] = []
            for blk in item["blocks"]:
                if blk[0] == "paragraph":
                    inner_parts.append(_render_inline(refs, blk[1]))
                else:
                    inner_parts.append("\n" + _render_block(refs, blk))
            out.append("<li" + li_class + ">" + checkbox + "".join(inner_parts) + "</li>\n")
        else:
            out.append("<li" + li_class + ">\n" + checkbox + _render_blocks(refs, item["blocks"]) + "</li>\n")
    return "".join(out)


# ── Public entry point ────────────────────────────────────────────────────────


def to_html(source: str) -> str:
    """Render GFM markdown ``source`` to deterministic, cross-host HTML."""
    if not source:
        return ""
    normalized = source.replace("\r\n", "\n").replace("\r", "\n")
    raw_lines = normalized.split("\n")
    refs, lines = _extract_ref_defs(raw_lines)
    blocks = _parse_blocks(lines)
    html = _render_blocks(refs, blocks)
    return sanitize_markdown_html(html)
