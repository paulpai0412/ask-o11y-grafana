"""Generic bounded relation-graph expansion for ontology dataset discovery."""
from __future__ import annotations

from collections import defaultdict, deque
from typing import Any


def relations(snapshot: dict[str, Any], include_proposed: bool = False) -> list[dict[str, Any]]:
    output = []
    for dataset in snapshot["registry"]["datasets"]:
        for relation in dataset.get("relations", []):
            approved = relation.get("status") == "approved" and bool(relation.get("executable"))
            if approved or include_proposed:
                output.append(relation)
    return output


def expand_datasets(snapshot: dict[str, Any], seeds: list[str], max_hops: int = 2, limit: int = 16, include_proposed: bool = False) -> dict[str, Any]:
    if not seeds or len(seeds) > 50 or not 0 <= max_hops <= 3 or not 1 <= limit <= 50:
        raise ValueError("invalid bounded ontology graph expansion")
    dataset_ids = {str(dataset["physical_id"]).upper(): str(dataset["physical_id"]) for dataset in snapshot["registry"]["datasets"]}
    normalized = []
    for seed in seeds:
        key = str(seed).upper()
        if key not in dataset_ids:
            raise ValueError(f"UNKNOWN_DATASET: {seed}")
        if dataset_ids[key] not in normalized:
            normalized.append(dataset_ids[key])
    graph: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    selected_relations = relations(snapshot, include_proposed)
    for relation in selected_relations:
        source, target = str(relation["from_dataset"]), str(relation["to_dataset"])
        graph[source.upper()].append((target, relation))
        graph[target.upper()].append((source, relation))
    queue = deque((seed, 0) for seed in normalized)
    distances = {seed.upper(): 0 for seed in normalized}
    parent: dict[str, tuple[str, dict[str, Any]]] = {}
    order = list(normalized)
    while queue and len(order) < limit:
        current, distance = queue.popleft()
        if distance >= max_hops:
            continue
        for neighbor, relation in sorted(graph[current.upper()], key=lambda item: (item[0], item[1]["canonical_id"])):
            key = neighbor.upper()
            if key in distances:
                continue
            distances[key] = distance + 1
            parent[key] = (current, relation)
            order.append(neighbor)
            queue.append((neighbor, distance + 1))
            if len(order) == limit:
                break
    paths = []
    included = {dataset_id.upper() for dataset_id in order}
    seen_relations: set[str] = set()
    for relation in selected_relations:
        if str(relation["from_dataset"]).upper() not in included or str(relation["to_dataset"]).upper() not in included:
            continue
        relation_id = str(relation["canonical_id"])
        if relation_id in seen_relations:
            continue
        seen_relations.add(relation_id)
        paths.append({"from": relation["from_dataset"], "to": relation["to_dataset"], "hop": max(distances[str(relation["from_dataset"]).upper()], distances[str(relation["to_dataset"]).upper()]), "relation": relation})
    return {"seeds": normalized, "datasets": order, "paths": paths, "max_hops": max_hops, "include_proposed": include_proposed, "truncated": bool(queue)}
