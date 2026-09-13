import json
import re

import pytest

from llm_bench.review_html import render_review
from llm_bench.review_metrics import METRIC_HELP


def test_private_viewer_cannot_execute_conversation_markup(tmp_path):
    path = tmp_path / "review.html"
    markup = "</script><script>alert(1)</script>"
    render_review([{"history": [{"role": "assistant", "content": markup}]}], path,
                  baseline={"model_profiles": {"Model A": markup}},
                  profile_notes={"Model A": markup})
    text = path.read_text()
    assert "</script><script>alert(1)" not in text
    assert "\\u003c/script>" in text
    assert "innerHTML" not in text
    assert path.stat().st_mode & 0o777 == 0o600


def test_shareable_export_removes_private_payload_and_keeps_metrics(tmp_path):
    path = tmp_path / "share.html"
    private = "PRIVATE_SENTINEL"
    row = {"project": private, "model": "Model A", "tested": 1, "ttft_s": 2.5,
           "cost_usd": 0.2, "extension": private, "native_calls": private}
    stat = {"project": private, "cohort": "Available cases", "model": "Model A",
            "metric": "TTFT", "unit": "s", "sample": "call in a complete conversation",
            "mean": 2.5, "min": 1, "max": 4, "n": 2, "extension": private}
    checks = []

    def guard(content):
        assert private not in content
        checks.append(content)

    render_review([{"project": private, "case": private, "model": "Model A", "status": "ok",
                    "metrics": row, "description": private, "scenario_name": private,
                    "history": [{"role": "system", "content": private},
                                {"role": "assistant", "tool_calls": [{"arguments": private}]}],
                    "raw_status": private, "extension": private}], path,
                  project_rows=[row], common_rows=[row], consolidated=[stat],
                  project_names={private: private}, profile_notes={"Model A": private},
                  model_profiles={"Model A": "reasoning: low"},
                  baseline={"consolidated": [stat], "model_profiles": {"Model A": "default"},
                            "history": private}, shareable=True, publication_check=guard)
    html = path.read_text()
    payload = json.loads(re.search(r'id="records">(.*?)</script>', html, re.S)[1])
    assert len(checks) == 2
    assert payload["records"][0]["project"] == "Project 1"
    assert payload["records"][0]["case"] == "Scenario 1"
    assert payload["records"][0]["history"] == []
    assert payload["records"][0]["metrics"]["ttft_s"] == 2.5
    assert payload["records"][0]["metrics"]["native_calls"] is None
    assert payload["consolidated"][0]["mean"] == 2.5
    assert payload["baseline"]["consolidated"][0]["n"] == 2
    assert payload["project_names"] == {}
    assert payload["profile_notes"] == {}
    assert "href=" not in html and "src=" not in html
    assert "../followup/" not in html


def test_shareable_requires_guard_and_rejects_before_writing(tmp_path):
    path = tmp_path / "share.html"
    with pytest.raises(ValueError, match="publication check"):
        render_review([], path, shareable=True)

    def reject(content):
        raise ValueError("Private label rejected")

    with pytest.raises(ValueError, match="Private label rejected"):
        render_review([], path, shareable=True, publication_check=reject)
    assert not path.exists()


def test_all_consolidated_metrics_have_help():
    from llm_bench.consolidated import consolidate

    rows = consolidate([], [], {}, [], {"model-a": "Model A"}, {"project_1": 1})
    assert {r["metric"] for r in rows} <= METRIC_HELP.keys()


def test_shareable_contains_readable_results_before_javascript(tmp_path):
    from html.parser import HTMLParser

    class TextReader(HTMLParser):
        def __init__(self):
            super().__init__()
            self.text = []

        def handle_data(self, data):
            self.text.append(data)

    records = [{"project": "Project 1", "case": str(i), "model": "Model A", "status": "ok",
                "metrics": {"tested": 1, "cost_usd": i / 100, "ttft_s": i + .25}, "history": []}
               for i in [1, 2]]
    stat = {"project": "All projects", "cohort": "Available cases", "model": "Model A",
            "metric": "TTFT", "unit": "s", "sample": "call in a complete conversation",
            "mean": 12.34, "min": 10, "max": 15, "n": 2}
    path = tmp_path / "readable.html"
    render_review(records, path, consolidated=[stat], baseline={"consolidated": [stat]},
                  model_profiles={"Model A": '<img src=x onerror="alert(1)">'},
                  shareable=True, publication_check=lambda _: None)
    html = path.read_text()
    static = html.split('<main id="static-report">')[1].split('</main>')[0]
    assert '<main id="static-report" hidden' not in html
    assert '<main id="interactive-report" hidden>' in html
    assert '<script' not in static and '<img' not in static
    reader = TextReader()
    reader.feed(static)
    text = ' '.join(reader.text)
    for expected in ["12.34", "10.00–15.00", "N = 2", "Scenario 1", "Scenario 2",
                     "1.25 s", "2.25 s", "USD 0.02000", "Initial vs current measurements",
                     "Reasoning-only deltas do not stop this timer"]:
        assert expected in text
