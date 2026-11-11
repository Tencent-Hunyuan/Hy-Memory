"""Behavior of the shared document/query analyzer through real FTS5 matching."""

import re
import sqlite3

import pytest

from hy_memory.data.chroma_lexical_analyzer import (
    ANALYZER_VERSION,
    analyze_document,
    compile_query,
)


def matches(content, query, *, tags=(), symbols=False):
    document = analyze_document(content, tags)
    compiled = compile_query(query)
    expression = compiled.symbols_match if symbols else compiled.terms_match
    if not expression:
        return False
    with sqlite3.connect(":memory:") as connection:
        connection.execute("CREATE VIRTUAL TABLE search USING fts5(terms, symbols)")
        connection.execute(
            "INSERT INTO search(terms,symbols) VALUES (?,?)",
            (document.terms, document.symbols),
        )
        return connection.execute(
            "SELECT count(*) FROM search WHERE search MATCH ?", (expression,),
        ).fetchone()[0] == 1


@pytest.mark.parametrize("content,query", [
    ("We debugged the CONNECTION reset", "connection"),
    ("修复ECONNRESET错误", "ECONNRESET"),
    ("采用东湖高新的方案", "东湖高新"),
    ("loadUserProfile failed", "profile"),
    ("HTTPServer.parseXML failed", "server"),
    ("foo_bar returns a value", "bar"),
    ("ｅｒｒｏｒ ＥＣＯＮＮＲＥＳＥＴ", "error"),
    ("部署于东湖高新", "东湖"),
])
def test_document_and_query_share_ordinary_terms(content, query):
    assert matches(content, query)


@pytest.mark.parametrize("content,query", [
    ("修复ECONNRESET错误", "ECONNRESET"),
    ("修复ECONNRESET错误", "econnreset"),
    ("loadUserProfile failed", "loadUserProfile"),
    ("foo_bar failed", "foo_bar"),
    ("project-omega released", "project-omega"),
    ("Compile with C++", "C++"),
    ("C# bindings", "C#"),
    ("发布v1.2.3版本", "v1.2.3"),
    ("Edit src/foo_bar.py", "src/foo_bar.py"),
    ("Edit src/foo_bar.py", "foo_bar.py"),
    (r"Edit C:\src\foo_bar.py", "C:/src/foo_bar.py"),
    ("Edit src/配置_loader.py", "src/配置_loader.py"),
    ("Edit 项目/模块.py", "模块.py"),
    ("配置文件在src/foo.py中", "src/foo.py"),
    ("配置文件在src/foo.py中", "foo.py"),
    ("配置文件在src/配置/foo.py中", "src/配置/foo.py"),
    ("配置文件在src/模块.py中", "src/模块.py"),
    ("配置文件在foo.py中", "foo.py"),
    ("编辑项目/模块.py中", "模块.py"),
    (r"配置文件在C:\src\foo.py中", "C:/src/foo.py"),
    ("ＥＣＯＮＮＲＥＳＥＴ", "ECONNRESET"),
])
def test_full_symbols_survive_tokenization(content, query):
    assert matches(content, query, symbols=True)


@pytest.mark.parametrize("content,query", [
    ("foo-bar", "foo_bar"),
    ("v1/2/3", "v1.2.3"),
    ("v1.2.3", "v1/2/3"),
    ("src/Foo.py", "src/foo.py"),
    ("other/foo.py", "src/foo.py"),
    ("ordinary connection troubleshooting", "connection"),
    ("foo_bar only", "foo_bar v1.2.3"),
    ("C language", "C++"),
])
def test_exact_symbols_do_not_collapse_distinct_values(content, query):
    assert not matches(content, query, symbols=True)


def test_chinese_query_requires_a_contiguous_bigram_phrase():
    assert not matches("东湖与高新之间另有说明", "东湖高新")
    assert not matches("东湖", "东湖高新", tags=["湖高新"])
    assert not matches("东湖 湖高 高新", "东湖高新")


def test_tags_are_indexed_without_merging_content_boundaries():
    assert matches("ordinary text", "ECONNRESET", tags=["ECONNRESET"], symbols=True)
    assert matches("ordinary text", "东湖高新", tags=["东湖高新"])


@pytest.mark.parametrize("query", ["", "  ", '"\'()[]{}/*+--', "..."])
def test_empty_and_punctuation_queries_have_no_branches(query):
    compiled = compile_query(query)
    assert not compiled.terms_match
    assert not compiled.symbols_match


@pytest.mark.parametrize("query", [
    '"unterminated', "OR", "NEAR(foo)", "foo) OR (bar", 'src/foo.py"',
])
def test_user_text_cannot_change_fts_expression_structure(query):
    # Success means MATCH parsed a compiler-owned expression, including malformed
    # quotes/operators in the user's text. The terms themselves remain literal.
    assert matches(query, query)


def test_token_storage_is_safe_ascii_and_has_a_stable_version():
    document = analyze_document("东湖ECONNRESET C++ src/配置_loader.py v1.2.3")
    assert re.fullmatch(r"[a-z0-9 ]*", document.terms)
    assert re.fullmatch(r"[a-z0-9 ]*", document.symbols)
    assert ANALYZER_VERSION


def test_repeated_analysis_is_deterministic_and_preserves_term_frequency():
    once = analyze_document("alpha")
    twice = analyze_document("alpha alpha")
    assert twice.terms.count(once.terms.strip()) == 2
    assert analyze_document("alpha alpha") == twice
