from pathlib import Path

import pytest

from app.services import attachments_service as svc
from app.services import conversations_service as convs


@pytest.fixture(autouse=True)
def _patch_attachments_dir(tmp_path, monkeypatch):
    # Must NOT be the same directory temp_db's sqlite file lives in — prune_orphans
    # walks this whole tree, and the DB file would look like an orphaned attachment.
    attachments_dir = tmp_path / "attachments"
    attachments_dir.mkdir()
    monkeypatch.setattr(svc, "ATTACHMENTS_DIR", attachments_dir)
    return attachments_dir


@pytest.fixture()
def conv_id(temp_db):
    return convs.create_conversation().id


def test_save_attachment_creates_file_and_row(temp_db, conv_id):
    att = svc.save_attachment(conv_id, "photo.png", b"fake-bytes", "image/png")
    assert att.original_name == "photo.png"
    assert att.size_bytes == len(b"fake-bytes")
    assert Path(att.stored_path).exists()
    assert Path(att.stored_path).read_bytes() == b"fake-bytes"


def test_filename_path_traversal_is_stripped(temp_db, conv_id, _patch_attachments_dir):
    att = svc.save_attachment(conv_id, "../../evil.png", b"x", "image/png")
    stored = Path(att.stored_path)
    # Must land inside the conversation's own directory, never escape it.
    assert stored.parent == _patch_attachments_dir / conv_id
    assert ".." not in stored.name


def test_filename_with_separators_is_sanitized(temp_db, conv_id):
    att = svc.save_attachment(conv_id, "sub/dir\\name.png", b"x", "image/png")
    assert Path(att.stored_path).parent.name == conv_id


def test_disallowed_extension_rejected(temp_db, conv_id):
    with pytest.raises(svc.AttachmentRejected):
        svc.save_attachment(conv_id, "malware.exe", b"x", None)


def test_oversized_file_rejected(temp_db, conv_id, monkeypatch):
    monkeypatch.setattr(svc, "MAX_UPLOAD_BYTES", 10)
    with pytest.raises(svc.AttachmentRejected):
        svc.save_attachment(conv_id, "big.txt", b"x" * 100, "text/plain")


def test_attach_to_message_links_rows(temp_db, conv_id):
    att = svc.save_attachment(conv_id, "a.txt", b"hi", "text/plain")
    msg = convs.add_message(conv_id, "user", "hi")
    svc.attach_to_message([att.id], msg.id)
    fetched = svc.get_attachment(att.id)
    assert fetched.message_id == msg.id


def test_delete_attachment_removes_row_and_file(temp_db, conv_id):
    att = svc.save_attachment(conv_id, "a.txt", b"hi", "text/plain")
    path = Path(att.stored_path)
    assert path.exists()
    svc.delete_attachment(att.id)
    assert svc.get_attachment(att.id) is None
    assert not path.exists()


def test_prune_orphans_removes_files_with_no_db_row(temp_db, conv_id, _patch_attachments_dir):
    orphan_dir = _patch_attachments_dir / conv_id
    orphan_dir.mkdir(parents=True, exist_ok=True)
    orphan_file = orphan_dir / "orphan.txt"
    orphan_file.write_text("no db row references me")

    removed = svc.prune_orphans()
    assert removed == 1
    assert not orphan_file.exists()


def test_prune_orphans_keeps_known_files(temp_db, conv_id):
    att = svc.save_attachment(conv_id, "keep.txt", b"hi", "text/plain")
    removed = svc.prune_orphans()
    assert removed == 0
    assert Path(att.stored_path).exists()
