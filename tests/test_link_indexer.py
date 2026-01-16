from __future__ import annotations

from sp.app import config, indexer


def setup_function(_function):
    config.set_active_vault(None)


def teardown_function(_function):
    config.set_active_vault(None)


def test_indexer_tracks_wiki_links(tmp_path):
    config.set_active_vault(str(tmp_path))
    page_path = "/Home/Home.md"
    content = """
# Home
[Projects:Roadmap|Roadmap]
[Notes|Notes](https://example.com)
[:Idea List|Idea List]
[./image.png|Screenshot]
"""
    indexer.index_page(page_path, content)

    relations = config.fetch_link_relations(page_path)
    assert set(relations["outgoing"]) == {
        "/Projects/Roadmap/Roadmap.md",
        "/Idea List/Idea List.md",
    }
    assert relations["incoming"] == []


def test_fetch_link_relations_incoming(tmp_path):
    config.set_active_vault(str(tmp_path))
    config.update_page_index(
        path="/Source/Source.md",
        title="Source",
        tags=[],
        links=["/Target/Target.md"],
        tasks=[],
    )
    config.update_page_index(
        path="/Target/Target.md",
        title="Target",
        tags=[],
        links=[],
        tasks=[],
    )

    relations = config.fetch_link_relations("/Target/Target.md")
    assert relations["incoming"] == ["/Source/Source.md"]
    assert relations["outgoing"] == []
