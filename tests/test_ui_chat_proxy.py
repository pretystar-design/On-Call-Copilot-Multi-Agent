import ui.server as ui_server


def test_build_chat_payload_uses_latest_user_message_and_session_id():
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "second"},
    ]

    payload = ui_server._build_chat_payload(messages, "session-123")

    assert payload == {"message": "second", "session_id": "session-123"}
