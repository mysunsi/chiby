from chibycore.llm_providers import MiniMaxProvider


def test_extract_openai_style():
    data = {
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "content": "[EXPLAIN] x\n[COMMAND] echo hi\n[DANGEROUS] false",
                }
            }
        ]
    }
    assert "echo hi" in MiniMaxProvider._extract_minimax_reply(data)


def test_extract_legacy_messages_text():
    data = {"choices": [{"messages": [{"text": "hello"}]}]}
    assert MiniMaxProvider._extract_minimax_reply(data) == "hello"
