import json

import pytest

from zotwatch.results.models import ArtifactReference
from zotwatch.results.publication import OutputPublisher, PublicationError


def render_text(value):
    def render(path):
        path.write_text(value, encoding="utf-8")
    return render


def test_successful_generation_is_authoritative_and_aliases_are_compatibility_only(tmp_path):
    reports = tmp_path / "reports"
    publisher = OutputPublisher(reports)
    published = publisher.publish(
        "run-1",
        {"recommendations.json": render_text('{"schema_version":1}\n'),
         "feed.xml": render_text("<rss/>"),
         "report.html": render_text("<html></html>")},
    )
    pointer = json.loads((reports / ".zotwatch-output/latest-success.json").read_text())
    assert pointer["generation_id"] == "run-1"
    assert {artifact.path for artifact in published.artifacts} == {
        ".zotwatch-output/generations/run-1/recommendations.json",
        ".zotwatch-output/generations/run-1/feed.xml",
        ".zotwatch-output/generations/run-1/report.html",
    }
    for artifact in published.artifacts:
        ArtifactReference.model_validate(artifact.model_dump(mode="json"))
        assert artifact.sha256 and artifact.size_bytes > 0 and artifact.publishable
    assert (reports / "recommendations.json").read_text() == '{"schema_version":1}\n'


def test_failed_render_preserves_previous_generation_and_aliases(tmp_path):
    reports = tmp_path / "reports"
    publisher = OutputPublisher(reports)
    publisher.publish("good", {"feed.xml": render_text("<rss>good</rss>")})
    pointer_before = (reports / ".zotwatch-output/latest-success.json").read_bytes()
    alias_before = (reports / "feed.xml").read_bytes()

    def fail(path):
        path.write_text("partial", encoding="utf-8")
        raise RuntimeError("secret upstream text")

    with pytest.raises(PublicationError):
        publisher.publish("bad", {"feed.xml": fail, "report.html": render_text("later")})
    assert (reports / ".zotwatch-output/latest-success.json").read_bytes() == pointer_before
    assert (reports / "feed.xml").read_bytes() == alias_before
    assert not (reports / ".zotwatch-output/generations/bad").exists()


@pytest.mark.parametrize("name", ["../secret", "/absolute", "nested/file", "unknown.txt"])
def test_output_allowlist_rejects_unsafe_artifact_names(tmp_path, name):
    with pytest.raises(PublicationError):
        OutputPublisher(tmp_path).publish("run-1", {name: render_text("x")})
