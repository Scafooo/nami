<h1>NAMI — city places for any personal interest, with every reason traceable to its source</h1>

<div align="center">
  <img src="assets/images/banner_logpose.svg" alt="NAMI: from the graph of an interest to the places of a city" style="max-width: 60%; height: auto;">
</div>

**NAMI** recommends the places of a city for a personal interest, however narrow — a person, a movement, a period, a theme — from a knowledge graph in which every fact carries its source and its confidence.
It follows the chains of facts that lead from the interest to places, ranks each place by the strongest chain and explains it by all of them, and weighs every fact by how far the operator trusts its source.
A language model may enrich a tour, but NAMI shows a fact the model adds only if the page it cites states it: whatever proposes a fact, its provenance decides if the visitor sees it.

This repository holds the code of NAMI, the city layer of Zurich, the benchmark of published walks, and the evaluation reported in the paper, with all the lists compared and the answers of the language models.

## 📱 In the app

NAMI runs inside a commercial tour-planning application, here in Zurich for a visitor interested in Einstein.

<table>
  <tr>
    <td align="center" width="20%"><img src="assets/images/app-01-einstein-stop.png" alt="A stop for an interest in Einstein, with its reason"></td>
    <td align="center" width="20%"><img src="assets/images/app-02-einstein-route.png" alt="The route through the stops"></td>
    <td align="center" width="20%"><img src="assets/images/app-03-city-map.png" alt="The map of the city"></td>
    <td align="center" width="20%"><img src="assets/images/app-04-reconstruction.png" alt="A place that no longer exists, with its story"></td>
    <td align="center" width="20%"><img src="assets/images/app-05-todays-gem.png" alt="A place suggested for today"></td>
  </tr>
  <tr>
    <td align="center"><sub>A stop for the interest in Einstein, with the reason it was chosen</sub></td>
    <td align="center"><sub>The walk through the stops</sub></td>
    <td align="center"><sub>The map of the city</sub></td>
    <td align="center"><sub>A place that no longer exists, with its story</sub></td>
    <td align="center"><sub>A place suggested for today</sub></td>
  </tr>
</table>

## 🧭 Quickstart

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
python src/build_store.py                        # the city layer, once
python src/main_best_first.py --interests Q937   # an interest in Albert Einstein (its Wikidata id)
```

Interests are given as Wikidata ids (`Q937` Albert Einstein, `Q6034` Dada, …), several at once if needed.
NAMI reads Wikidata on demand, at a polite rate and with its own User-Agent, and caches what it reads; a first query takes minutes, later ones seconds.
`--ignore-provenance` and the other flags of `src/main_best_first.py --help` switch off one part at a time, as in the ablations of the paper.

## 🗺️ Project layout

```
src/
  domain/                  the method: path valuation, best-first retrieval, stops, tour composition
  application/             the use case that answers an interest
  infrastructure/          fact sources (Wikidata, city layer), the annotated store, profiles
  pipeline/                how the city layer is built from the open data of Zurich
  main_best_first.py       the command line
mappings/                  relation weights, source trust, the relations read from text
data/geo-ontology/         the city layer: plaques, public art, street dedications, memorials, with their sources
evaluation/
  ground_truth_zurich.json the benchmark: 639 stops that 136 published walks, guides and institutions give for 35 interests
  BENCHMARK_PROTOCOL.md    how it was built
  suite/                   the experiment: settings, NAMI and baseline runs, model prompts and answers, lists, results
  annotazioni/             the workbooks for the assessors, and the scripts that prepare them and read them back
```

## 📜 Where the results are

| In the paper | In this repository |
|---|---|
| The lists compared (NAMI, the two models, NAMI + model, the guides) | `evaluation/suite/lists/*.json`: every place, its rank and all its reasons with their sources |
| What the models were asked and what they answered | `evaluation/suite/llm/`: the prompts, block by block, and the answers as given |
| Which reasons the check shows | `evaluation/suite/runs/llm/*.json`: each reason with the page it cites, its quoted sentence and the outcome of the check |
| The judgements of people | `evaluation/annotazioni/da_annotare/`: the workbooks as given to the assessors; `evaluation/suite/annotations_keys/`: which list proposed each row |
| The benchmark of published walks | `evaluation/ground_truth_zurich.json` |

## 🔁 Reproducing the evaluation

```bash
cd evaluation/annotazioni
python prepara.py               # runs NAMI and the baselines, builds the lists and the workbooks
python calcola_risultati.py     # from the filled workbooks (in compilati/) to the tables of the paper
```

The language models were used through their public chat interfaces, with web search, each prompt in a new temporary chat; their answers are kept as given, so that the lists can be rebuilt without asking them again.

## 📦 Data and licences

- **Wikidata**, CC0, read on demand.
- **Zürich Tourismus** open data: the catalogue of places (`data/geo-ontology/ABox/pois.ttl`), CC BY-SA 4.0.
- **City of Zurich** open data: heritage plaques, public art (Kunst im Stadtraum), street dedications.
- **OpenStreetMap** memorials and artworks, © OpenStreetMap contributors, ODbL.
- The benchmark lists the walks and guides it draws on, each with its URL; their texts are not copied here.

---

<div align="center" style="margin-top: 2em; opacity: 0.6;">
  <img src="assets/images/onepiece_flag.jpg" alt="Straw Hat Pirates" height="40"><br>
  <sub><em>"I'm going so I can draw a map of the entire world."</em> — Nami, <em>One Piece</em>, episode 53</sub><br><br>
  <sub><strong>Clima-Tact</strong> — The weather staff of Nami, the navigator of the Straw Hat Pirates, with which she turns the weather into her weapon.</sub>
</div>
