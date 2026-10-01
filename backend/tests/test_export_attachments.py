"""Export must LIST a message's attachments by name and size -- and must never
include their contents or stored path.

`list_messages()` stays attachment-free by design (other callers depend on it);
export reads `list_messages_with_attachments()` so it can render one
`Attachments:` line for every message that has files.
"""

from pathlib import Path

import pytest

from app.services import attachments_service as att_svc
from app.services import conversations_service as svc


@pytest.fixture(autouse=True)
def _patch_attachments_dir(tmp_path, monkeypatch):
    # Keep uploads out of the shared session dir (and out of the DB directory),
    # matching test_services_attachments.
    d = tmp_path / "attachments"
    d.mkdir()
    monkeypatch.setattr(att_svc, "ATTACHMENTS_DIR", d)
    return d


def _attach(conv_id, msg_id, name, data, mime):
    att = att_svc.save_attachment(conv_id, name, data, mime)
    att_svc.attach_to_message([att.id], msg_id)
    return att


def test_export_lists_both_attachments_with_mime_and_human_size(temp_db):
    conv = svc.create_conversation()
    msg = svc.add_message(conv.id, "user", "two files")
    _attach(conv.id, msg.id, "diagram.png", b"x" * 21, "image/png")
    _attach(conv.id, msg.id, "notes.txt", b"y" * 3482, "text/plain")

    out = svc.export_as_markdown(conv.id)

    line = next(l for l in out.splitlines() if l.startswith("Attachments:"))
    assert "diagram.png (image/png, 21 B)" in line
    assert "notes.txt (text/plain, 3.4 KB)" in line
    # Both entries are comma-separated on the single line.
    assert line.count("(") == 2
    assert line.count(")") == 2


def test_export_omits_attachment_contents_and_stored_path(temp_db):
    conv = svc.create_conversation()
    msg = svc.add_message(conv.id, "user", "please read this")
    secret = "TOP-SECRET-CONTENT-98765"
    att = _attach(conv.id, msg.id, "secret.txt", secret.encode(), "text/plain")

    out = svc.export_as_markdown(conv.id)

    assert "secret.txt (text/plain," in out  # the name IS listed
    assert secret not in out
    assert att.stored_path not in out
    assert Path(att.stored_path).name not in out  # uuid-prefixed stored filename


def test_message_without_attachments_has_no_attachments_line(temp_db):
    conv = svc.create_conversation()
    svc.add_message(conv.id, "user", "no files here")
    svc.add_message(conv.id, "assistant", "understood")

    out = svc.export_as_markdown(conv.id)

    assert "Attachments:" not in out


def test_attachments_line_appears_only_for_the_message_with_files(temp_db):
    conv = svc.create_conversation()
    svc.add_message(conv.id, "user", "plain question")
    with_file = svc.add_message(conv.id, "user", "see attached")
    _attach(conv.id, with_file.id, "notes.txt", b"hi", "text/plain")

    out = svc.export_as_markdown(conv.id)

    assert out.count("Attachments:") == 1


def test_list_messages_stays_attachment_free(temp_db):
    conv = svc.create_conversation()
    msg = svc.add_message(conv.id, "user", "hi")
    _attach(conv.id, msg.id, "a.txt", b"hi", "text/plain")

    assert not hasattr(svc.list_messages(conv.id)[0], "attachments")
