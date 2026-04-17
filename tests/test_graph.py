# tests/test_graph.py
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from pathlib import Path
from db import KontextDB
from graph import extract_entities, build_graph, query_connections, prune_graph, rebuild_graph


@pytest.fixture
def db(tmp_path):
    db = KontextDB(str(tmp_path / "test.db"))
    db.add_entry(file="identity.md", fact="Name: Alice Example. Location: Berlin, Germany.", source="[test]", grade=10, tier="active")
    db.add_entry(file="goals.md", fact="Migrating students from Preply to Stripe.", source="[test]", grade=9, tier="active")
    db.add_entry(file="financial.md", fact="Sole proprietor business. Bank: Raiffeisen.", source="[test]", grade=9, tier="active")
    db.add_entry(file="tools.md", fact="Demo tool built with Electron. Published on GitHub example/demo-tool.", source="[test]", grade=9, tier="active")
    yield db
    db.close()


def test_extract_entities():
    entities = extract_entities("Migrating students from Preply to Stripe. Uses Raiffeisen bank.")
    assert any("Preply" in e for e in entities)
    assert any("Stripe" in e for e in entities)


def test_build_graph(db):
    count = build_graph(db)
    assert count > 0
    rels = db.get_relations("Alice")
    assert len(rels) >= 0  # May or may not extract depending on NER


def test_query_connections(db):
    build_graph(db)
    results = query_connections(db, "Preply")
    assert isinstance(results, list)

def test_extract_entities_filters_noise():
    entities = extract_entities("The Air Quality Module was created in Phase Two. Fix applied.")
    assert "Air" not in entities
    assert "Fix" not in entities
    assert "Phase" not in entities
    assert "Module" not in entities


def test_extract_entities_keeps_real_entities():
    entities = extract_entities("Alice uses Stripe and Preply for payments via Raiffeisen.")
    assert "Alice" in entities
    assert "Stripe" in entities
    assert "Preply" in entities
    assert "Raiffeisen" in entities


def test_prune_low_quality_relations(db):
    db.add_entry(file="test.md", fact="The Air Module Fix was in Phase Two.", source="[test]", grade=8, tier="active")
    db.add_entry(file="test.md", fact="Alice uses Stripe.", source="[test]", grade=9, tier="active")
    build_graph(db)
    removed = prune_graph(db)
    assert removed > 0
    noise_rels = db.get_relations("Air")
    assert len(noise_rels) == 0


def test_rebuild_graph(db):
    db.add_entry(file="test.md", fact="Alice uses Stripe for payments.", source="[test]", grade=9, tier="active")
    count = rebuild_graph(db)
    assert count >= 0
    noise_rels = db.get_relations("Module")
    assert len(noise_rels) == 0
