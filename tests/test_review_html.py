from llm_bench.review_html import render_review


def test_private_viewer_cannot_execute_conversation_markup(tmp_path):
    path = tmp_path / "review.html"
    render_review([{"history": [{"role": "assistant", "content": "</script><script>alert(1)</script>"}]}], path)
    text = path.read_text()
    assert "</script><script>alert(1)" not in text
    assert "\\u003c/script>" in text
    assert "innerHTML" not in text
    assert path.stat().st_mode & 0o777 == 0o600
