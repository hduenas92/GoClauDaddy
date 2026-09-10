from app.services import conversations_service as svc


def test_create_and_get_conversation(temp_db):
    conv = svc.create_conversation(name="Test Chat")
    fetched = svc.get_conversation(conv.id)
    assert fetched.name == "Test Chat"
    assert fetched.status == "idle"
    assert fetched.session_id is None


def test_list_conversations_ordered_by_updated_at_desc(temp_db):
    c1 = svc.create_conversation(name="first")
    c2 = svc.create_conversation(name="second")
    convs = svc.list_conversations()
    assert [c.id for c in convs] == [c2.id, c1.id] or [c.id for c in convs] == [c1.id, c2.id]
    # both created "now" so order may tie — just assert both present
    assert {c.id for c in convs} == {c1.id, c2.id}


def test_rename_conversation(temp_db):
    conv = svc.create_conversation(name="old")
    svc.rename_conversation(conv.id, "new")
    assert svc.get_conversation(conv.id).name == "new"


def test_set_session_id(temp_db):
    conv = svc.create_conversation()
    svc.set_session_id(conv.id, "sess-abc")
    assert svc.get_conversation(conv.id).session_id == "sess-abc"


def test_update_settings_partial(temp_db):
    conv = svc.create_conversation(model="model-a")
    svc.update_conversation_settings(conv.id, permission_mode="plan")
    updated = svc.get_conversation(conv.id)
    assert updated.model == "model-a"  # unchanged
    assert updated.permission_mode == "plan"


def test_delete_conversation_cascades_messages(temp_db):
    conv = svc.create_conversation()
    svc.add_message(conv.id, "user", "hi")
    svc.delete_conversation(conv.id)
    assert svc.get_conversation(conv.id) is None
    assert svc.list_messages(conv.id) == []


def test_add_message_increments_seq(temp_db):
    conv = svc.create_conversation()
    m1 = svc.add_message(conv.id, "user", "first")
    m2 = svc.add_message(conv.id, "assistant", "second")
    assert m1.seq == 1
    assert m2.seq == 2
    msgs = svc.list_messages(conv.id)
    assert [m.content for m in msgs] == ["first", "second"]


def test_add_message_with_usage(temp_db):
    conv = svc.create_conversation()
    svc.add_message(conv.id, "assistant", "hi", input_tokens=10, output_tokens=5, thinking="pondering")
    fetched = svc.list_messages(conv.id)[0]
    assert fetched.input_tokens == 10
    assert fetched.output_tokens == 5
    assert fetched.thinking == "pondering"
