"""5-2 — SQL injection surface.

Of 68 execute/executemany/executescript calls in backend/app, 59 are plain
parameterised statements and need nothing said about them.

(The commit that introduced this file, fcb541e, says 69. That figure was wrong
by one: the sweep's regex counted `# conn.execute() keeps the DDL and version
write ...` at db/migrations.py:193, which is a COMMENT, not a call site. The
error was caught by a second count that disagreed, which is the only reason it
was caught at all -- a lone count has nothing to be wrong against.) Nine splice
something into the SQL string with an f-string. Every one of the nine splices
an IDENTIFIER or a run of placeholders, never a value:

    UPDATE conversations SET {', '.join(fields)} WHERE id = ?
    UPDATE projects      SET {', '.join(fields)} WHERE id = ?
    SELECT ... WHERE id IN ({','.join('?' * len(ids))})
    SELECT ... WHERE id IN ({placeholders})
    SELECT ... {scope_sql} ...                 (one of two literal strings)
    conn.executescript(sql)                    (migration bodies, module constants)

That is a safe shape, and the reason it is safe is worth stating because it is
not obvious from the line: placeholders cannot bind a column NAME, so building
a SET clause by concatenation is the normal way to write a partial update. It
stays safe only while the concatenated fragments are strings the CODE chose.
The moment one of them is derived from a request — a key from a PATCH body, a
sort column from a query string — the bound `?` for the id becomes decoration
and the caller is writing SQL.

So the invariant under test is not "this query is safe today". It is: THE
FRAGMENTS SPLICED INTO A SET CLAUSE ARE LITERALS IN THE SOURCE. That is what a
future refactor would break, and it is checkable mechanically.

Checked with `ast`, not a regex: a regex over `fields.append(...)` cannot tell
a literal from an f-string that happens to contain quotes, and this is exactly
the kind of check that is worth nothing if it can be fooled by formatting.
"""
import ast
import pathlib

import pytest

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)

BACKEND = pathlib.Path(__file__).resolve().parents[1]
APP_DIR = BACKEND / "app"

# Every list that is joined into a SET clause. Named explicitly rather than
# discovered, so that a NEW service building SQL this way does not quietly
# inherit a passing test it was never checked by — it has to be added here,
# which is the moment someone looks at it.
SET_CLAUSE_BUILDERS = [
    ("services/conversations_service.py", "fields"),
    ("services/projects_service.py", "fields"),
]


def _appended_values(path: pathlib.Path, list_name: str):
    """Every expression passed to `<list_name>.append(...)` in the file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "append"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == list_name
            and node.args
        ):
            out.append((node.lineno, node.args[0]))
    return out


@pytest.mark.parametrize("rel,list_name", SET_CLAUSE_BUILDERS)
def test_set_clause_fragments_are_string_literals(rel, list_name):
    path = APP_DIR / rel
    appended = _appended_values(path, list_name)

    # NON-EMPTY-SET GUARD, first. If the AST walk finds nothing — the file was
    # renamed, the list was renamed, the pattern changed — then "every fragment
    # is a literal" is vacuously true of zero fragments, and this test would
    # pass having checked nothing. That failure mode has cost this project a
    # session before. It fails loudly instead.
    assert appended, (
        f"no `{list_name}.append(...)` calls found in {rel} — this test would "
        f"pass over an empty set. If the code moved, move the test."
    )

    offenders = [
        (lineno, ast.dump(node))
        for lineno, node in appended
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str))
    ]
    assert not offenders, (
        f"{rel}: a fragment spliced into a SET clause is not a string literal. "
        f"Placeholders cannot bind a column name, so whatever is here is written "
        f"straight into the SQL: {offenders}"
    )


def test_injection_in_a_patched_value_is_stored_not_executed(temp_db):
    """The value half: a payload in a PATCHed field must be data, not SQL.

    The classic escape — close the quote, add another assignment — aimed at a
    sibling column. If it were interpreted, `working_dir` would change; if it
    is bound, `name` comes back as the payload verbatim and `working_dir` is
    untouched.

    Asserting BOTH halves is the point. "working_dir unchanged" alone would
    also hold if the PATCH had simply failed, and a rejected request proves
    nothing about parameter binding.
    """
    home = str(pathlib.Path.home())
    proj = client.post("/api/projects", json={"name": "Victim", "working_dir": home}).json()

    payload = "x', working_dir='/pwned"
    res = client.patch(f"/api/projects/{proj['id']}", json={"name": payload})
    assert res.status_code == 200, res.text

    after = client.get(f"/api/projects/{proj['id']}").json()
    assert after["name"] == payload, "the payload was not stored verbatim as data"
    assert after["working_dir"] == home, (
        "working_dir changed — the payload was interpreted as SQL, not bound as a value"
    )


def test_injection_in_a_search_query_does_not_error_or_execute(temp_db):
    """`q` goes to an FTS5 MATCH, which has its own grammar.

    The risk here is not classic injection — q binds via `MATCH ?` — but that
    hostile punctuation reaches the FTS parser and surfaces as a 500. A 400 is
    a fine answer; a 200 is a fine answer; a 500 means the input reached
    something that could not handle it.
    """
    for q in ("' OR 1=1 --", '" OR ""="', "x'); DROP TABLE messages; --", "*", "NEAR/"):
        res = client.get("/api/search", params={"q": q})
        assert res.status_code != 500, f"q={q!r} produced a 500: {res.text[:200]}"


def test_conversations_still_intact_after_the_probes(temp_db):
    """A drop-table payload that worked would take the table with it.

    Cheap, and it is the assertion that would actually notice. The tests above
    check responses; this one checks the database is still there afterwards.
    """
    res = client.get("/api/conversations")
    assert res.status_code == 200
    assert isinstance(res.json(), list)
