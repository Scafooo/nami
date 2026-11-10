"""Builds the Zurich geo-KG store (config.STORE_DIR) from the checked-in
TBox/ABox Turtle files under data/geo-ontology/. store/ is gitignored, so
every fresh clone needs this once before main.py, run_scenarios.py, or
replay_evaluation.py can find any data; rerun it after editing the Turtle
files themselves (a fresh build, not an incremental one).

Run: python src/build_store.py
"""

from __future__ import annotations

import config
from infrastructure.repositories.knowledge_graph.knowledge_graph_repository import KnowledgeGraphRepositoryImpl


def main() -> None:
    KnowledgeGraphRepositoryImpl().load(config.GEO_TBOX_FILES, config.GEO_ABOX_FILES, store_dir=config.STORE_DIR)
    print(f"Built -> {config.STORE_DIR}")


if __name__ == "__main__":
    main()
