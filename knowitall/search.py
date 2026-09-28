"""Turns what a person types in the filter box into an FTS5 query (and a LIKE fallback).

Syntax:  words are ANDed            engineer remote
         "quoted phrase"            "machine learning"
         -word  or  NOT word        engineer -intern      engineer NOT manager
         a OR b                     backend OR platform   (binds tighter than the implicit AND)
Single words match as prefixes ("eng" finds engineer) and expand through knowitall.geo, so "nyc" also
finds "New York" and "germany" also finds rows whose country is DE. Quoted phrases match exactly.
Everything is quoted before it reaches SQLite, so no input can produce an FTS syntax error.
"""
import re

from . import geo

TOKEN_RE = re.compile(r'-?"[^"]*"|\S+')
WORDY = re.compile(r"\w", re.UNICODE)
MAX_TERMS = 12
PLACE_MARK = "\x00"            # marks consecutive plain words that form one known place, e.g. "bay area"
MAX_EXPANSION = 200            # alternatives per term; region groups are the largest


def _strip_quotes(token):
    return token[1:-1] if len(token) >= 2 and token[0] == token[-1] == '"' else token.strip('"')


def _merge_places(tokens):
    """'bay area' typed without quotes is one place, not the words 'bay' and 'area'."""
    def plain(token):
        return not token.startswith(("-", '"')) and token.upper() not in ("AND", "OR", "NOT") and WORDY.search(token)

    merged, i = [], 0
    while i < len(tokens):
        for size in (3, 2):
            window = tokens[i:i + size]
            if len(window) == size and all(plain(t) for t in window) and geo.is_known_place(" ".join(window)):
                merged.append(PLACE_MARK + " ".join(window))
                i += size
                break
        else:
            merged.append(tokens[i])
            i += 1
    return merged


def parse(text):
    """-> (clauses, negatives). clauses: [[alt, ...], ...] (each list is OR'd, lists are ANDed);
    negatives: [term, ...]. A term is ('word'|'phrase'|'place', text)."""
    tokens = _merge_places(TOKEN_RE.findall(text or ""))
    clauses, negatives = [], []
    i = 0

    def term_of(token):
        if token.startswith(PLACE_MARK):
            return ("place", token[1:])
        quoted = token.startswith('"') or token.startswith('-"')
        body = _strip_quotes(token.lstrip("-")) if quoted else token.lstrip("-").strip('"')
        if not WORDY.search(body):
            return None
        body = body.strip()
        return ("phrase" if quoted or " " in body else "word", body)

    while i < len(tokens) and len(clauses) + len(negatives) < MAX_TERMS:
        token = tokens[i]
        upper = token.upper()
        if upper == "NOT" and i + 1 < len(tokens):
            term = term_of(tokens[i + 1].lstrip("-"))
            if term:
                negatives.append(term)
            i += 2
            continue
        if token.startswith("-") and len(token) > 1:
            term = term_of(token)
            if term:
                negatives.append(term)
            i += 1
            continue
        if upper in ("AND", "OR", "NOT"):                # a dangling operator is just ignored
            i += 1
            continue
        term = term_of(token)
        i += 1
        if term is None:
            continue
        alternatives = [term]
        while i + 1 < len(tokens) and tokens[i].upper() == "OR":
            nxt = term_of(tokens[i + 1])
            i += 2
            if nxt:
                alternatives.append(nxt)
        clauses.append(alternatives)
    return clauses, negatives


def _phrase(text):
    return '"' + text.replace('"', '""') + '"'


def _alternatives(term):
    """[(column|None, phrase, prefix?)] for one parsed term, expansions included."""
    kind, text = term
    if kind == "phrase":
        return [(None, text, False)]
    prefix = kind == "word" and len(re.sub(r"\W", "", text)) >= 2
    alternatives = [(None, text, prefix)]
    for column, phrase in geo.expand_term(text)[1:MAX_EXPANSION]:
        alternatives.append((column, phrase, False))
    return alternatives


def _fts_term(term):
    parts = []
    for column, phrase, prefix in _alternatives(term):
        expr = _phrase(phrase) + (" *" if prefix else "")
        parts.append(f"{column} : {expr}" if column else expr)
    return "(" + " OR ".join(parts) + ")"


def to_fts(text):
    """(positive_query | None, negative_query | None) for FTS5 MATCH."""
    clauses, negatives = parse(text)
    positive = " AND ".join("(" + " OR ".join(_fts_term(t) for t in clause) + ")" for clause in clauses) or None
    negative = " OR ".join(_fts_term(t) for t in negatives) or None
    return positive, negative


# ---------- LIKE fallback (SQLite built without FTS5) ----------

HAYSTACK = ("lower(coalesce(p.title,'') || ' ' || coalesce(p.company,'') || ' ' || coalesce(p.location,'')"
            " || ' ' || coalesce(p.department,'') || ' ' || coalesce(p.city,'') || ' ' || coalesce(p.region,''))")


def _like_escape(text):
    return text.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _like_term(term):
    sql, params = [], []
    for column, phrase, _ in _alternatives(term):
        if column == "country":
            sql.append("p.country = ?")
            params.append(phrase)
        else:
            sql.append(f"{HAYSTACK} LIKE ? ESCAPE '\\'")
            params.append(f"%{_like_escape(phrase)}%")
    return "(" + " OR ".join(sql) + ")", params


def to_like(text):
    """(sql, params) using LIKE; sql is '' when the text has no searchable terms."""
    clauses, negatives = parse(text)
    sql, params = [], []
    for clause in clauses:
        parts = [_like_term(t) for t in clause]
        sql.append("(" + " OR ".join(p[0] for p in parts) + ")")
        params += [x for p in parts for x in p[1]]
    for term in negatives:
        part_sql, part_params = _like_term(term)
        sql.append(f"NOT {part_sql}")
        params += part_params
    return " AND ".join(sql), params
