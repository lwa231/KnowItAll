import pytest

from knowitall import search


def kinds(terms):
    return [(k, t) for k, t in terms]


def test_words_are_anded_and_negatives_collected():
    clauses, negatives = search.parse('senior engineer -intern NOT manager')
    assert [c[0] for c in clauses] == [("word", "senior"), ("word", "engineer")]
    assert negatives == [("word", "intern"), ("word", "manager")]


def test_quoted_phrases_and_negated_phrases():
    clauses, negatives = search.parse('"machine learning" python -"vice president"')
    assert clauses == [[("phrase", "machine learning")], [("word", "python")]]
    assert negatives == [("phrase", "vice president")]


def test_or_binds_tighter_than_and():
    clauses, _ = search.parse("backend OR platform engineer")
    assert clauses == [[("word", "backend"), ("word", "platform")], [("word", "engineer")]]
    clauses, _ = search.parse("a OR b OR c")
    assert len(clauses) == 1 and len(clauses[0]) == 3


def test_junk_is_ignored():
    for text in ("", None, "   ", "---", '""', "AND", "OR", "NOT", "+++ !!!"):
        assert search.parse(text) == ([], []), text
    assert search.to_fts("") == (None, None)


def test_fts_query_quotes_prefixes_and_expands():
    positive, negative = search.to_fts("nyc engineer -intern")
    assert '"nyc" *' in positive and '"New York"' in positive and '"engineer" *' in positive
    assert negative == '("intern" *)'
    positive, _ = search.to_fts("germany")
    assert 'country : "DE"' in positive


def test_phrases_are_exact_not_prefixed_or_expanded():
    positive, _ = search.to_fts('"new york"')
    assert positive == '(("new york"))'


def test_quotes_and_operators_cannot_break_the_query():
    positive, negative = search.to_fts('c++ "a"" b" ) ( NEAR AND OR * : ^ }')
    assert positive and "NEAR" not in positive.replace('"NEAR"', "")


def test_single_letter_words_are_not_prefixed():
    positive, _ = search.to_fts("c")
    assert '"c" *' not in positive and '"c"' in positive


def test_term_limit():
    clauses, negatives = search.parse(" ".join(f"w{i}" for i in range(50)))
    assert len(clauses) + len(negatives) == search.MAX_TERMS


def test_like_fallback_shape():
    sql, params = search.to_like("engineer -intern germany")
    assert sql.count("LIKE") >= 3 and "NOT (" in sql and "p.country = ?" in sql
    assert "%engineer%" in params and "DE" in params
    assert search.to_like("") == ("", [])
    sql, params = search.to_like("100%")
    assert "%100\\%%" in params                      # LIKE wildcards in the input are escaped


def test_unquoted_known_places_merge_but_quoted_ones_stay_exact():
    clauses, _ = search.parse("bay area engineer")
    assert clauses == [[("place", "bay area")], [("word", "engineer")]]
    clauses, _ = search.parse("new york")
    assert clauses == [[("place", "new york")]]
    clauses, _ = search.parse('"bay area"')
    assert clauses == [[("phrase", "bay area")]]
    clauses, _ = search.parse("big area")                   # not a place: two ordinary words
    assert [c[0][0] for c in clauses] == ["word", "word"]
    positive, _ = search.to_fts("bay area")
    assert '"San Francisco"' in positive and '"bay area" *' not in positive
