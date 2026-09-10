import pytest

from app.services import projects_service as svc


def test_create_project_with_valid_dir(temp_db, tmp_path):
    project = svc.create_project("Test Project", str(tmp_path))
    assert project.name == "Test Project"
    assert project.working_dir == str(tmp_path)


def test_create_project_rejects_relative_path(temp_db):
    with pytest.raises(svc.InvalidWorkingDirError):
        svc.create_project("Bad", "relative/path")


def test_create_project_rejects_nonexistent_dir(temp_db, tmp_path):
    missing = tmp_path / "does-not-exist"
    with pytest.raises(svc.InvalidWorkingDirError):
        svc.create_project("Bad", str(missing))


def test_create_project_rejects_file_not_directory(temp_db, tmp_path):
    file_path = tmp_path / "a_file.txt"
    file_path.write_text("hi")
    with pytest.raises(svc.InvalidWorkingDirError):
        svc.create_project("Bad", str(file_path))


def test_update_project_working_dir_validated(temp_db, tmp_path):
    project = svc.create_project("P", str(tmp_path))
    with pytest.raises(svc.InvalidWorkingDirError):
        svc.update_project(project.id, working_dir="relative")


def test_delete_project_detaches_conversations_not_deletes_them(temp_db, tmp_path):
    from app.services import conversations_service as convs

    project = svc.create_project("P", str(tmp_path))
    conv = convs.create_conversation(project_id=project.id)
    svc.delete_project(project.id)
    fetched = convs.get_conversation(conv.id)
    assert fetched is not None
    assert fetched.project_id is None
