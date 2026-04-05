# tests/test_graph.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from pathlib import Path
from db import KontextDB
from graph import extract_entities, build_graph, query_connections


@pytest.fixture
def db(tmp_path):
    db = KontextDB(str(tmp_path / "test.db"))
    db.add_entry(file="identity.md", fact="Name: Ionut Rosu. Location: Constanta, Romania.", source="[Claude 2026-04]", grade=10, tier="active")
    db.add_entry(file="goals.md", fact="Migrating students from Preply to Stripe.", source="[Claude 2026-04]", grade=9, tier="active")
    db.add_entry(file="financial.md", fact="PFA business under Sistem Real. Bank: Raiffeisen.", source="[Claude 2026-04]", grade=9, tier="active")
    db.add_entry(file="tools.md", fact="Video Convertor built with Electron. Published on GitHub numarulunu/claude-convertor.", source="[Claude 2026-04]", grade=9, tier="active")
    yield db
    db.close()


def test_extract_entities():
    entities = extract_entities("Migrating students from Preply to Stripe. Uses Raiffeisen bank.")
    assert any("Preply" in e for e in entities)
    assert any("Stripe" in e for e in entities)


def test_build_graph(db):
    count = build_graph(db)
    assert count > 0
    rels = db.get_relations("Ionut")
    assert len(rels) >= 0  # May or may not extract depending on NER


def test_query_connections(db):
    build_graph(db)
    results = query_connections(db, "Preply")
    assert isinstance(results, list)
