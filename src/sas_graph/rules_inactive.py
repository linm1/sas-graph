"""Inactive comment evidence only (section 11.6)."""

import re


_DATA_RE = re.compile(r"^data\s+([\w.&]+(?:\s+[\w.&]+)*)\s*;$", re.IGNORECASE)
_INPUT_RE = re.compile(r"^(?:set|merge)\s+([\w.&]+(?:\s+[\w.&]+)*)\s*;$", re.IGNORECASE)
_RUN_RE = re.compile(r"^run\s*;$", re.IGNORECASE)
_MACRO_RE = re.compile(r"&([A-Za-z_][A-Za-z0-9_]*)\.?")


def _source(comment, rule, original_text=None, line_start=None, line_end=None):
    return {
        "file": comment.file,
        "line_start": comment.line_start if line_start is None else line_start,
        "line_end": comment.line_end if line_end is None else line_end,
        "statement_order": comment.after_statement_order,
        "original_text": comment.original_text if original_text is None else original_text,
        "rule": rule,
    }


def _body(comment):
    text = comment.original_text
    if comment.kind == "block":
        return text[2:-2]
    return text[2:-1] if comment.kind == "macro_star" else text[1:-1]


def _statements(comment):
    """Return only an unambiguously complete, commented DATA step."""
    body = _body(comment)
    statements = []
    start = 0
    while (end := body.find(";", start)) != -1:
        raw = body[start:end + 1]
        if raw.strip():
            original = raw.strip("\r\n")
            leading = raw[:len(raw) - len(raw.lstrip("\r\n"))]
            statements.append((
                original.strip(), original,
                comment.line_start + body[:start].count("\n") + leading.count("\n"),
            ))
        start = end + 1
    text_only = [text for text, _, _ in statements]
    if (
        len(text_only) < 3
        or sum(_DATA_RE.match(text) is not None for text in text_only) != 1
        or not _DATA_RE.match(text_only[0])
        or not _RUN_RE.match(text_only[-1])
    ):
        return []
    if any(not (_DATA_RE.match(text) or _INPUT_RE.match(text) or _RUN_RE.match(text)) for text in text_only):
        return []
    return statements


def _evidence(ctx, comment, kind, label):
    node_id = ctx.next_inactive_id("inactiveevidence")
    ctx.add_node(node_id, "InactiveEvidence", label, evidence_kind=kind,
                 source=_source(comment, f"inactive_{kind}"))
    return node_id


def apply(comments, ctx):
    """Emit evidence without parsing it as active SAS or resolving macros."""
    for comment in comments:
        block_id = ctx.next_inactive_id("commentblock")
        ctx.add_node(block_id, "CommentBlock", comment.text, kind=comment.kind,
                     source=_source(comment, "comment_block"))
        statements = _statements(comment)
        if not statements:
            continue

        evidence_by_dataset = {}
        evidence_by_macro = {}

        def dataset(raw):
            key = raw.lower()
            if key not in evidence_by_dataset:
                evidence_by_dataset[key] = _evidence(ctx, comment, "dataset", raw)
            return evidence_by_dataset[key]

        def macro(name):
            key = name.lower()
            if key not in evidence_by_macro:
                evidence_by_macro[key] = _evidence(ctx, comment, "macro", name)
            return evidence_by_macro[key]

        target_ids = []
        input_entries = []
        for text, original_text, line in statements:
            statement_id = ctx.next_inactive_id("commentedstatement")
            statement_source = _source(
                comment, "commented_statement", original_text, line,
                line + original_text.count("\n"),
            )
            ctx.add_node(statement_id, "CommentedStatement", text,
                         source=statement_source)
            for name in dict.fromkeys(_MACRO_RE.findall(text)):
                evidence_id = macro(name)
                ctx.add_edge(
                    "comment_mentions_macro", block_id, evidence_id,
                    {**statement_source, "rule": "comment_mentions_macro"},
                    macro_name=name.lower(),
                )
            data_match = _DATA_RE.match(text)
            input_match = _INPUT_RE.match(text)
            if data_match:
                target_ids = [dataset(raw) for raw in data_match.group(1).split()]
                for raw, evidence_id in zip(data_match.group(1).split(), target_ids):
                    ctx.add_edge("comment_mentions_dataset", block_id, evidence_id,
                                 {**statement_source, "rule": "comment_mentions_dataset"}, dataset_name=raw)
                    ctx.add_edge("inactive_candidate_writes", statement_id, evidence_id,
                                 {**statement_source, "rule": "inactive_candidate_writes"})
            elif input_match:
                input_ids = [dataset(raw) for raw in input_match.group(1).split()]
                input_entries.extend((evidence_id, statement_source) for evidence_id in input_ids)
                for raw, evidence_id in zip(input_match.group(1).split(), input_ids):
                    ctx.add_edge("comment_mentions_dataset", block_id, evidence_id,
                                 {**statement_source, "rule": "comment_mentions_dataset"}, dataset_name=raw)
                    ctx.add_edge("inactive_candidate_reads", statement_id, evidence_id,
                                 {**statement_source, "rule": "inactive_candidate_reads"})

        dependency_pairs = set()
        for target_id in target_ids:
            for input_id, input_source in input_entries:
                if (target_id, input_id) in dependency_pairs:
                    continue
                dependency_pairs.add((target_id, input_id))
                ctx.add_edge("inactive_candidate_depends_on", target_id, input_id,
                             {**input_source, "rule": "inactive_candidate_depends_on"})
