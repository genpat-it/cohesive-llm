"""
Structural Knowledge Graph for the Nextflow Component Catalog.

Fully implements the Graphify query, traversal, scoring, and reasoning engine:
- Edge confidence tiers:
    EXTRACTED  — from tool_graph.json (AST-derived real pipeline wiring: A.out.channel | B)
    INFERRED   — from usage index co-occurrence (component A and B appear together in template code)
    AMBIGUOUS  — channel-name semantic match only (fallback for variable naming similarity)
- Trigram candidate indexing + IDF-weighted tiered scoring with coverage dampening
- BFS (broad context) and DFS (chain tracing) with degree-based hub thresholding
- Complete induced subgraph edges & distance-ranked token-budget formatting
- Node inspection (explain), neighbor inspection, shortest path with hop breakdown,
  god-node identification, community discovery, and Graphify JSON export.
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
from array import array
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, NamedTuple, Optional, Set, Tuple

import networkx as nx
from networkx.readwrite import json_graph

from core.utils.logger import logger
from core.plugin_loader import get_active_plugin


# ─────────────────────────────────────────────────────────────────────────────
# Graphify Stopwords & Relational Intent Terms
# ─────────────────────────────────────────────────────────────────────────────

_QUERY_STOPWORDS: frozenset[str] = frozenset({
    # Common English fillers & question words
    "how", "what", "why", "when", "where", "which", "who", "whom", "whose",
    "does", "did", "is", "are", "was", "were", "be", "been", "being",
    "can", "could", "should", "would", "will", "shall", "may", "might", "must",
    "has", "have", "had", "the", "and", "but", "not", "for", "from", "with",
    "without", "into", "onto", "off", "that", "this", "these", "those", "there",
    "here", "its", "their", "them", "they", "about", "any", "all", "some",
    "a", "an", "or", "in", "on", "at", "by", "to",
    # Generic pipeline/Nextflow query noise
    "step", "module", "tool", "pipeline", "workflow", "build",
    "create", "make", "run", "file", "data", "generate", "process",
    "using", "work", "works", "working",
})

_RELATIONAL_INTENT_TERMS: frozenset[str] = frozenset({
    "call", "calls", "called", "caller", "callers",
    "invoke", "invokes", "invoked",
    "use", "uses", "used", "using",
    "import", "imports", "imported",
    "export", "exports", "exported",
    "extend", "extends", "extended",
    "implement", "implements", "implemented",
    "depend", "depends",
    "reference", "references", "referenced",
    "feed", "feeds", "fed",
    "pipe", "pipes", "piped",
    "emit", "emits", "emitted",
    "take", "takes", "taken",
    "connect", "connects", "connected", "connection", "connections",
    "link", "links", "linked",
    "map", "maps", "mapped",
})


def _strip_diacritics(text: str | None) -> str:
    import unicodedata
    if not isinstance(text, str):
        text = "" if text is None else str(text)
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def _search_tokens(text: str) -> list[str]:
    """Split text into word tokens, stripping punctuation and diacritics."""
    return re.findall(r"\w+", _strip_diacritics(str(text)).lower())


def _is_searchable(term: str) -> bool:
    if all("a" <= ch <= "z" for ch in term):
        return len(term) > 2
    return True


def _query_terms(question: str) -> list[str]:
    """Extract searchable content terms from a natural-language query with synonym expansion."""
    terms: list[str] = []
    for tok in re.findall(r"\w+", question.lower()):
        if _is_searchable(tok):
            terms.append(tok)
    content = [t for t in terms if t not in _QUERY_STOPWORDS]
    base_terms = content or terms

    try:
        from core.plugin_loader import get_active_plugin
        plugin = get_active_plugin()
        syns = plugin.query_synonyms if plugin else {}
        expanded = list(base_terms)
        for t in base_terms:
            for canonical, syn_list in syns.items():
                if t == canonical or t in syn_list:
                    expanded.append(canonical)
                    expanded.extend(syn_list[:3])
        return list(dict.fromkeys(expanded))
    except Exception:
        return base_terms




def _trigrams(text: str) -> set[str]:
    """Character trigrams of text; for <3-char text the whole string is the key."""
    if len(text) < 3:
        return {text} if text else set()
    return {text[i:i + 3] for i in range(len(text) - 2)}


def _node_search_text(data: dict, nid: str) -> str:
    """Concatenate every field used for node matching into a searchable string."""
    norm_label = data.get("norm_label") or _strip_diacritics(data.get("label") or "").lower()
    label_tokens = " ".join(_search_tokens(data.get("label") or ""))
    tool = str(data.get("tool", "")).lower()
    domain = str(data.get("domain", "")).lower()
    description = str(data.get("description", "")).lower()
    inputs = " ".join(str(x).lower() for x in data.get("inputs", []))
    outputs = " ".join(str(x).lower() for x in data.get("outputs", []))
    nid_text = str(nid).lower()
    fields = (norm_label, label_tokens, nid_text, tool, domain, description, inputs, outputs)
    return "\x00".join(fields)


def _get_trigram_index(G: nx.Graph) -> dict:
    """Lazily build and cache a trigram -> node-position postings map on the graph."""
    idx = G.graph.get("_trigram_index")
    if idx is not None:
        return idx
    ids = list(G.nodes())
    postings: dict[str, array] = {}
    for i, nid in enumerate(ids):
        for g in _trigrams(_node_search_text(G.nodes[nid], nid)):
            bucket = postings.get(g)
            if bucket is None:
                bucket = array("i")
                postings[g] = bucket
            bucket.append(i)
    idx = {"ids": ids, "postings": postings, "set_cache": {}}
    G.graph["_trigram_index"] = idx
    return idx


def _trigram_candidates(G: nx.Graph, needles: list[str], *, guard_frac: float = 0.25) -> list[str] | None:
    """Node IDs whose text could contain any needle as a substring via the trigram index."""
    idx = _get_trigram_index(G)
    ids, postings, set_cache = idx["ids"], idx["postings"], idx["set_cache"]
    n = len(ids)
    if n == 0:
        return []
    needles = [s for s in needles if s]
    thresh = int(n * guard_frac)
    for s in needles:
        tgs = _trigrams(s)
        if not tgs or any(len(g) < 3 for g in tgs):
            return None  # too short to trigram-filter
        present = [len(postings[g]) for g in tgs if g in postings]
        if not present:
            continue
        if min(present) > thresh:
            return None  # rarest trigram still too common -> fallback to scan
    cand: set[int] = set()
    for s in needles:
        sets: list[set] | None = []
        for g in _trigrams(s):
            bucket = postings.get(g)
            if bucket is None:
                sets = None
                break
            cached = set_cache.get(g)
            if cached is None:
                cached = set(bucket)
                set_cache[g] = cached
            sets.append(cached)
        if not sets:
            continue
        sets.sort(key=len)
        hit = set(sets[0])
        for other in sets[1:]:
            hit &= other
            if not hit:
                break
        cand |= hit
    return [ids[i] for i in sorted(cand)]


_EXACT_MATCH_BONUS = 1000.0
_PREFIX_MATCH_BONUS = 100.0
_SUBSTRING_MATCH_BONUS = 1.0
_METADATA_MATCH_BONUS = 0.5


def _compute_idf(G: nx.Graph, terms: list[str]) -> dict[str, float]:
    """Compute IDF weights for query terms, cached in G.graph['_idf_cache']."""
    cache: dict[str, float] = G.graph.setdefault("_idf_cache", {})
    N = G.number_of_nodes() or 1
    uncached = [t for t in terms if t not in cache]
    if uncached:
        df: dict[str, int] = {t: 0 for t in uncached}
        for _, data in G.nodes(data=True):
            search_text = (
                (data.get("label") or "") + " "
                + (data.get("tool") or "") + " "
                + (data.get("domain") or "") + " "
                + (data.get("description") or "")
            ).lower()
            for t in uncached:
                if t in search_text:
                    df[t] += 1
        for t in uncached:
            cache[t] = math.log(1 + N / (1 + df[t]))
    return {t: cache.get(t, math.log(1 + N)) for t in terms}


class _QueryScores(NamedTuple):
    ranked: list[tuple[float, str]]
    best_seed_by_term: dict[str, str]


def _score_query(G: nx.Graph, terms: list[str], *, collect_per_term_seeds: bool = True) -> _QueryScores:
    """Graphify multi-tier query scorer with quadratic coverage scaling."""
    norm_terms = list(dict.fromkeys(tok for t in terms for tok in _search_tokens(t)))
    n_terms = len(norm_terms) or 1
    idf = _compute_idf(G, norm_terms)
    joined = " ".join(norm_terms)
    joined_w = max((idf.get(t, 1.0) for t in norm_terms), default=1.0)

    candidate_ids = _trigram_candidates(G, norm_terms + ([joined] if joined else []))
    node_iter = (
        G.nodes(data=True) if candidate_ids is None
        else ((nid, G.nodes[nid]) for nid in candidate_ids)
    )

    scored: list[tuple[float, str]] = []
    best_by_term: dict[str, tuple[tuple, str]] | None = {} if collect_per_term_seeds else None

    for nid, data in node_iter:
        label = (data.get("label") or nid).lower()
        tool = str(data.get("tool") or "").lower()
        domain = str(data.get("domain") or "").lower()
        description = str(data.get("description") or "").lower()
        inputs = [str(x).lower() for x in data.get("inputs", [])]
        outputs = [str(x).lower() for x in data.get("outputs", [])]
        channels_str = " ".join(inputs + outputs)
        nid_lower = nid.lower()

        score = 0.0

        # Full-query tier
        if joined:
            if joined in (label, tool, nid_lower):
                score += _EXACT_MATCH_BONUS * 10 * joined_w
            elif label.startswith(joined) or tool.startswith(joined) or nid_lower.startswith(joined):
                score += _PREFIX_MATCH_BONUS * 10 * joined_w

        matched = 0
        tiered = 0.0
        for t in norm_terms:
            w = idf.get(t, 1.0)
            tier_value = 0.0
            substr_value = 0.0
            meta_value = 0.0

            if t == label or t == tool or t == nid_lower:
                tier_value = _EXACT_MATCH_BONUS * w
                matched += 1
            elif label.startswith(t) or tool.startswith(t) or nid_lower.startswith(t):
                tier_value = _PREFIX_MATCH_BONUS * w
                matched += 1
            elif t in label or t in tool:
                substr_value = _SUBSTRING_MATCH_BONUS * w
                score += substr_value
                matched += 1

            if t in domain or t in description or t in channels_str:
                meta_value = _METADATA_MATCH_BONUS * w
                score += meta_value
                if not (t in label or t in tool):
                    matched += 1

            tiered += tier_value

            if collect_per_term_seeds and best_by_term is not None:
                singleton = 0.0
                if t in (label, tool, nid_lower):
                    singleton = _EXACT_MATCH_BONUS * 10 * w
                elif label.startswith(t) or tool.startswith(t) or nid_lower.startswith(t):
                    singleton = _PREFIX_MATCH_BONUS * 10 * w
                singleton += tier_value + substr_value + meta_value
                if singleton > 0:
                    key = (-singleton, -G.degree(nid), len(label), nid)
                    cur = best_by_term.get(t)
                    if cur is None or key < cur[0]:
                        best_by_term[t] = (key, nid)

        if tiered:
            score += tiered * (matched / n_terms) ** 2
        if score > 0:
            scored.append((score, nid))

    scored.sort(key=lambda s: (-s[0], len(G.nodes[s[1]].get("label") or s[1]), s[1]))
    best_seed_by_term: dict[str, str] = {}
    if collect_per_term_seeds and best_by_term:
        best_seed_by_term = {t: nid for t, (_k, nid) in best_by_term.items()}
    return _QueryScores(ranked=scored, best_seed_by_term=best_seed_by_term)


def _pick_seeds(
    scored: list[tuple[float, str]],
    max_k: int = 4,
    gap_ratio: float = 0.20,
    *,
    G: nx.Graph | None = None,
    best_seed_by_term: dict[str, str] | None = None,
) -> list[str]:
    """Select seeds with gap filtering, label deduplication, and per-term guarantee."""
    if not scored:
        return []
    top_score = scored[0][0]
    seeds: list[str] = []
    seen_labels: set[str] = set()

    for score, nid in scored:
        if len(seeds) >= max_k:
            break
        if seeds and score < top_score * gap_ratio:
            break
        label = (G.nodes[nid].get("label") if G else nid) or nid
        norm_key = label.lower()
        if norm_key in seen_labels:
            continue
        seen_labels.add(norm_key)
        seeds.append(nid)

    if G is not None and best_seed_by_term:
        for term in sorted(best_seed_by_term):
            best_nid = best_seed_by_term[term]
            label = G.nodes[best_nid].get("label", best_nid)
            norm_key = label.lower()
            if best_nid not in seeds and norm_key not in seen_labels:
                seen_labels.add(norm_key)
                seeds.append(best_nid)

    return seeds


def _complete_induced_edges(G: nx.Graph, visited: set[str], edges_seen: list[tuple]) -> None:
    """Capture cross-edges and seed-to-seed connections within the visited subgraph."""
    directed = G.is_directed()

    def _key(u: str, v: str):
        return (u, v) if directed else frozenset((u, v))

    seen = {_key(u, v) for u, v in edges_seen}
    for u in sorted(visited):
        neighbors = G.successors(u) if directed else G.neighbors(u)
        for v in neighbors:
            if u == v or v not in visited:
                continue
            k = _key(u, v)
            if k in seen:
                continue
            seen.add(k)
            edges_seen.append((u, v))


def _bfs(G: nx.Graph, start_nodes: list[str], depth: int) -> tuple[set[str], list[tuple]]:
    """Hub-guarded Breadth-First Search traversal."""
    degrees = [G.degree(n) for n in G.nodes()]
    if degrees:
        degrees_sorted = sorted(degrees)
        p99_idx = int(len(degrees_sorted) * 0.99)
        hub_threshold = max(15, degrees_sorted[p99_idx])
    else:
        hub_threshold = 15

    seed_set = set(start_nodes)
    visited: set[str] = set(start_nodes)
    frontier = set(start_nodes)
    edges_seen: list[tuple] = []

    for _ in range(depth):
        next_frontier: set[str] = set()
        for n in frontier:
            if n not in seed_set and G.degree(n) >= hub_threshold:
                continue
            if G.is_directed():
                for nb in G.successors(n):
                    if nb not in visited:
                        next_frontier.add(nb)
                        edges_seen.append((n, nb))
                for nb in G.predecessors(n):
                    if nb not in visited:
                        next_frontier.add(nb)
                        edges_seen.append((nb, n))
            else:
                for nb in G.neighbors(n):
                    if nb not in visited:
                        next_frontier.add(nb)
                        edges_seen.append((n, nb))
        visited.update(next_frontier)
        frontier = next_frontier

    _complete_induced_edges(G, visited, edges_seen)
    return visited, edges_seen


def _dfs(G: nx.Graph, start_nodes: list[str], depth: int) -> tuple[set[str], list[tuple]]:
    """Depth-First Search traversal for tracing sequential dependency chains."""
    degrees = [G.degree(n) for n in G.nodes()]
    if degrees:
        degrees_sorted = sorted(degrees)
        p99_idx = int(len(degrees_sorted) * 0.99)
        hub_threshold = max(15, degrees_sorted[p99_idx])
    else:
        hub_threshold = 15

    seed_set = set(start_nodes)
    visited: set[str] = set()
    edges_seen: list[tuple] = []
    stack = [(n, 0) for n in reversed(start_nodes)]

    while stack:
        node, d = stack.pop()
        if node in visited or d > depth:
            continue
        visited.add(node)
        if node not in seed_set and G.degree(node) >= hub_threshold:
            continue
        neighbors = G.successors(node) if G.is_directed() else G.neighbors(node)
        for nb in neighbors:
            if nb not in visited:
                stack.append((nb, d + 1))
                edges_seen.append((node, nb))

    _complete_induced_edges(G, visited, edges_seen)
    return visited, edges_seen


def _subgraph_to_text(
    G: nx.Graph,
    nodes: set[str],
    edges: list[tuple],
    token_budget: int = 2000,
    *,
    seeds: list[str] | None = None,
) -> str:
    """Render the extracted subgraph to formatted text with distance decay and budget protection."""
    char_budget = token_budget * 3
    lines: list[str] = []
    seed_set = set(seeds or [])
    seed_hits = [n for n in (seeds or []) if n in nodes]

    # Rank non-seed nodes by hop distance from seeds
    def _adj(n):
        if G.is_directed():
            yield from G.successors(n)
            yield from G.predecessors(n)
        else:
            yield from G.neighbors(n)

    dist: dict[str, int] = {n: 0 for n in seed_hits}
    frontier, hop = list(seed_hits), 0
    while frontier:
        hop += 1
        nxt = []
        for n in frontier:
            for nb in _adj(n):
                if nb in nodes and nb not in dist:
                    dist[nb] = hop
                    nxt.append(nb)
        frontier = nxt

    ordered = seed_hits + sorted(
        nodes - seed_set,
        key=lambda n: (dist.get(n, 999), -G.degree(n), str(n)),
    )

    for nid in ordered:
        d = G.nodes[nid]
        in_ch = ", ".join(d.get("inputs", [])) or "none"
        out_ch = ", ".join(d.get("outputs", [])) or "none"
        tool = d.get("tool") or ""
        tool_str = f" tool={tool}" if tool else ""
        comm = d.get("community", "?")
        conf_counts = {"EXTRACTED": 0, "INFERRED": 0, "AMBIGUOUS": 0}
        for _, _, edata in G.edges(nid, data=True):
            conf_counts[edata.get("confidence", "AMBIGUOUS")] += 1
        star = "★ " if nid in seed_set else "  "
        lines.append(
            f"{star}NODE {nid}{tool_str} "
            f"[in={in_ch} | out={out_ch} | community={comm} | "
            f"E={conf_counts['EXTRACTED']} I={conf_counts['INFERRED']} A={conf_counts['AMBIGUOUS']}]"
        )

    lines.append("")
    for u, v in edges:
        if u in nodes and v in nodes and G.has_edge(u, v):
            edata = G[u][v]
            ch = edata.get("channel", "")
            conf = edata.get("confidence", "AMBIGUOUS")
            rel = edata.get("relation", "dataflow")
            ch_str = f" via={ch}" if ch else ""
            lines.append(f"EDGE {u} --{rel} [{conf}{ch_str}]--> {v}")

    lines.append(
        "\nNOTE: EXTRACTED=real Nextflow AST wiring, INFERRED=template co-usage, "
        "AMBIGUOUS=channel-name hint only"
    )

    output = "\n".join(lines)
    if len(output) > char_budget:
        cut_at = output[:char_budget].rfind("\n")
        cut_at = cut_at if cut_at > 0 else char_budget
        if seed_hits:
            seed_block_end = sum(len(lines[i]) + 1 for i in range(len(seed_hits))) - 1
            cut_at = max(cut_at, min(seed_block_end, len(output)))
        total_nodes = sum(1 for l in lines if "NODE " in l)
        shown_nodes = output[:cut_at].count("NODE ")
        cut_count = total_nodes - shown_nodes
        if cut_count > 0:
            output = (
                f"[!] TRUNCATED: showing {shown_nodes} of {total_nodes} nodes "
                f"(~{token_budget}-token budget).\n\n"
                + output[:cut_at]
                + f"\n... (truncated — {cut_count} more nodes cut by ~{token_budget}-token budget)"
            )

    return output


# ─────────────────────────────────────────────────────────────────────────────
# KnowledgeGraph Class
# ─────────────────────────────────────────────────────────────────────────────

class KnowledgeGraph:
    """
    Graphify-powered knowledge graph service for the Nextflow Component Catalog.

    Provides full AST graph reasoning, community detection, BFS/DFS traversal,
    node explanation, neighbor inspection, and shortest path finding.
    """

    def __init__(self):
        self.G: nx.DiGraph = nx.DiGraph()

        # Backward-compat adjacency dicts
        self.component_takes: Dict[str, Set[str]] = defaultdict(set)
        self.component_emits: Dict[str, Set[str]] = defaultdict(set)
        self.channel_consumed_by: Dict[str, Set[str]] = defaultdict(set)
        self.channel_produced_by: Dict[str, Set[str]] = defaultdict(set)
        self.usage_weights: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))

        # Communities & Caches
        self.communities: Dict[int, List[str]] = {}
        self.is_built = False
        self.query_synonyms: dict = {}

        # Topological Inverted Indices & Bipartite Partitioning
        self._tool_to_vertex: Dict[str, str] = {}
        self._alias_index: Dict[str, str] = {}
        self._helper_closures: Set[str] = set()
        self._valid_vertices: Set[str] = set()

    @property
    def nx_graph(self) -> nx.DiGraph:
        """Alias property for backward compatibility."""
        return self.G

    def normalize_channel(self, ch: str) -> str:
        ch = ch.lower().strip()
        if ".out." in ch:
            ch = ch.split(".out.")[-1].strip()
        if ch in ("assembly", "assembled", "scaffolds", "contigs", "fasta"):
            return "semantic_assembly"
        if ch in ("reads", "trimmed", "rawreads", "clean_reads", "data", "fastq", "fastq.gz"):
            return "semantic_reads"
        for canonical, synonyms in self.query_synonyms.items():
            if ch == canonical or ch in synonyms:
                return f"semantic_{canonical}"
        return f"semantic_{ch}"

    def build_nx_graph(self, store: Any) -> None:
        """Build the nx.DiGraph from all catalog sources in store."""
        if store:
            self.store = store
        if self.is_built:
            return

        try:
            from core.plugin_loader import get_active_plugin
            plugin = get_active_plugin()
            self.query_synonyms = plugin.query_synonyms if plugin else {}
        except Exception:
            self.query_synonyms = {}



        self.store = store
        logger.info("knowledge_graph_build_start")

        # 1. Nodes from components
        components = store.search(("components",), limit=5000)
        for comp in components:
            comp_id = comp.key
            data = comp.value
            inputs = data.get("input_channels") or data.get("input_types") or []
            outputs = data.get("output_channels") or data.get("out") or []
            tool_name = str(data.get("tool", "")).strip()

            self.G.add_node(
                comp_id,
                label=comp_id,
                description=data.get("description", "")[:250],
                inputs=inputs,
                outputs=outputs,
                tool=tool_name,
                domain=data.get("domain", ""),
                community=None,
            )

            # Build inverted indices for topological projection
            if tool_name:
                self._tool_to_vertex[tool_name.lower()] = comp_id
                if "__" in tool_name:
                    self._tool_to_vertex[tool_name.split("__")[-1].lower()] = comp_id
                elif "_" in tool_name:
                    self._tool_to_vertex[tool_name.split("_")[-1].lower()] = comp_id

            if "__" in comp_id:
                suffix = comp_id.split("__")[-1].lower()
                self._alias_index[suffix] = comp_id
                for sub in suffix.split("_"):
                    if len(sub) > 2 and sub not in self._alias_index:
                        self._alias_index[sub] = comp_id

            for kw in (data.get("keywords") or []):
                kw_clean = str(kw).lower().strip()
                if kw_clean and kw_clean not in self._alias_index:
                    self._alias_index[kw_clean] = comp_id

            for ch in inputs:
                norm_ch = self.normalize_channel(ch)
                self.component_takes[comp_id].add(norm_ch)
                self.channel_consumed_by[norm_ch].add(comp_id)
                if ch.lower().strip() == "data":
                    self.channel_consumed_by["semantic_reads"].add(comp_id)
                    self.channel_consumed_by["semantic_assembly"].add(comp_id)
            for ch in outputs:
                norm_ch = self.normalize_channel(ch)
                self.component_emits[comp_id].add(norm_ch)
                self.channel_produced_by[norm_ch].add(comp_id)
                if ch.lower().strip() == "data":
                    self.channel_produced_by["semantic_reads"].add(comp_id)
                    self.channel_produced_by["semantic_assembly"].add(comp_id)

        # 1B. Nodes from templates
        templates = store.search(("templates",), limit=5000)
        for tmpl in templates:
            tmpl_id = tmpl.key
            tmpl_data = tmpl.value
            if not self.G.has_node(tmpl_id):
                self.G.add_node(
                    tmpl_id,
                    label=tmpl_id,
                    description=str(tmpl_data.get("description", ""))[:250],
                    inputs=tmpl_data.get("input_channels") or [],
                    outputs=tmpl_data.get("output_channels") or [],
                    tool="template",
                    domain=tmpl_data.get("domain", "template"),
                    community=None,
                )
            self._alias_index[tmpl_id.lower()] = tmpl_id
            for cu in tmpl_data.get("components_used", []):
                cu_clean = str(cu).strip()
                if cu_clean:
                    self._alias_index[cu_clean.lower()] = cu_clean
                    if "__" in cu_clean:
                        suffix = cu_clean.split("__")[-1].lower()
                        self._alias_index[suffix] = cu_clean
                        self._tool_to_vertex[suffix] = cu_clean
                        for sub in suffix.split("_"):
                            if len(sub) > 2 and sub not in self._alias_index:
                                self._alias_index[sub] = cu_clean

        # 1C. Helper closures from active plugin
        try:
            from core.plugin_loader import get_active_plugin
            plugin = get_active_plugin()
            if plugin and hasattr(plugin, "helper_imports"):
                self._helper_closures = set(plugin.helper_imports.keys())
        except Exception:
            self._helper_closures = set()

        self._valid_vertices = set(self.G.nodes())

        # 1D. Dynamic On-the-Fly AST Code Mining (Zero-Metadata Dependency)
        self._mine_plugin_ast_semantics(store)

        logger.info("knowledge_graph_nodes_loaded", count=self.G.number_of_nodes())

        # 2. AMBIGUOUS edges from channel-name matches
        for comp_a in list(self.G.nodes()):
            for ch in self.component_emits.get(comp_a, []):
                for comp_b in self.channel_consumed_by.get(ch, []):
                    if comp_a != comp_b and not self.G.has_edge(comp_a, comp_b):
                        self.G.add_edge(
                            comp_a, comp_b,
                            relation="dataflow",
                            channel=ch,
                            confidence="AMBIGUOUS",
                            weight=1,
                        )

        # 3. INFERRED edges from template usage co-occurrence
        usages = store.search(("usage",), limit=5000)
        for usage in usages:
            comp_id = usage.key
            if not self.G.has_node(comp_id):
                continue
            for u in usage.value.get("usages", []):
                snippet = u.get("snippet", "")
                for other_comp in list(self.G.nodes()):
                    if other_comp == comp_id:
                        continue
                    if other_comp in snippet:
                        self.usage_weights[comp_id][other_comp] += 1
                        if self.G.has_edge(comp_id, other_comp):
                            current_conf = self.G[comp_id][other_comp].get("confidence", "AMBIGUOUS")
                            if current_conf == "AMBIGUOUS":
                                self.G[comp_id][other_comp]["confidence"] = "INFERRED"
                            self.G[comp_id][other_comp]["weight"] = (
                                self.G[comp_id][other_comp].get("weight", 1) + 1
                            )
                        else:
                            self.G.add_edge(
                                comp_id, other_comp,
                                relation="co_usage",
                                channel="",
                                confidence="INFERRED",
                                weight=2,
                            )

        # 4. EXTRACTED edges from AST tool_graph.json
        graph_item = store.get(("graph",), "adjacency")
        if graph_item and graph_item.value:
            graph_data = graph_item.value
            extracted_count = 0
            for edge in graph_data.get("edges", []):
                src = edge.get("source") or edge.get("from")
                tgt = edge.get("target") or edge.get("to")
                via = edge.get("via", [])
                channel_str = ", ".join(via) if isinstance(via, list) else str(via)

                if not src or not tgt:
                    continue
                if not self.G.has_node(src):
                    self.G.add_node(src, label=src, description="", inputs=[], outputs=[])
                if not self.G.has_node(tgt):
                    self.G.add_node(tgt, label=tgt, description="", inputs=[], outputs=[])

                if self.G.has_edge(src, tgt):
                    self.G[src][tgt]["confidence"] = "EXTRACTED"
                    self.G[src][tgt]["weight"] = self.G[src][tgt].get("weight", 1) + 5
                    if channel_str:
                        self.G[src][tgt]["channel"] = channel_str
                else:
                    self.G.add_edge(
                        src, tgt,
                        relation=edge.get("edge_type", "dataflow"),
                        channel=channel_str,
                        confidence="EXTRACTED",
                        weight=5,
                    )
                extracted_count += 1
            logger.info("knowledge_graph_extracted_edges", count=extracted_count)

        # 5. Community detection
        self._run_community_detection()

        self.is_built = True
        n_extracted = sum(1 for _, _, d in self.G.edges(data=True) if d.get("confidence") == "EXTRACTED")
        n_inferred = sum(1 for _, _, d in self.G.edges(data=True) if d.get("confidence") == "INFERRED")
        n_ambiguous = sum(1 for _, _, d in self.G.edges(data=True) if d.get("confidence") == "AMBIGUOUS")
        logger.info(
            "knowledge_graph_build_complete",
            nodes=self.G.number_of_nodes(),
            edges=self.G.number_of_edges(),
            extracted=n_extracted,
            inferred=n_inferred,
            ambiguous=n_ambiguous,
            communities=len(self.communities),
        )

    def _run_community_detection(self) -> None:
        """Partition graph into communities using Louvain algorithm."""
        if self.G.number_of_nodes() == 0:
            return
        undirected = self.G.to_undirected()
        try:
            coms = nx.community.louvain_communities(undirected, seed=42)
            self.communities = {}
            for i, nodes in enumerate(coms):
                self.communities[i] = list(nodes)
                for node_id in nodes:
                    if self.G.has_node(node_id):
                        self.G.nodes[node_id]["community"] = i
            logger.info("community_detection_complete", communities=len(self.communities))
        except Exception as e:
            logger.warning("community_detection_failed", error=str(e))

    def _mine_plugin_ast_semantics(self, store: Any) -> None:
        """Dynamically parse and extract AST tokens, tool names, parameters, and comments
        from all code assets in the active plugin directly into memory.
        Enables 100% zero-metadata dependency without modifying static JSON files.
        """
        import re
        from core.loader import data_loader

        code_items: Dict[str, str] = {}
        try:
            for item in store.search(("code",), limit=5000):
                if item and item.value:
                    code_items[item.key] = str(item.value.get("content", ""))
        except Exception:
            pass

        # Fallback / supplement with in-memory code_db
        for k, v in getattr(data_loader, "code_db", {}).items():
            if k not in code_items and isinstance(v, str):
                code_items[k] = v

        for comp_or_tmpl_id, code in code_items.items():
            if not code or not isinstance(code, str):
                continue

            target_node = self.project_vertex(comp_or_tmpl_id) or comp_or_tmpl_id

            # 1. Extract included process names: include { name } from './...'
            include_matches = re.findall(r'include\s*\{\s*([a-zA-Z0-9_,\s;]+)\s*\}\s*from', code)
            for inc in include_matches:
                for name in re.split(r'[,;]', inc):
                    name_clean = name.strip()
                    if name_clean and len(name_clean) > 2:
                        self._alias_index[name_clean.lower()] = target_node
                        self._tool_to_vertex[name_clean.lower()] = target_node
                        if not self.G.has_node(name_clean):
                            self.G.add_node(name_clean, label=name_clean, description=f"Included AST process '{name_clean}'", inputs=[], outputs=[])
                        if not self.G.has_node(comp_or_tmpl_id):
                            self.G.add_node(comp_or_tmpl_id, label=comp_or_tmpl_id, description="", inputs=[], outputs=[])
                        self.G.add_edge(comp_or_tmpl_id, name_clean, relation="includes", confidence="EXTRACTED", weight=10)

            # 2. Extract process definitions: process NAME { ... }
            proc_matches = re.findall(r'\bprocess\s+([a-zA-Z0-9_]+)\s*\{', code)
            for proc in proc_matches:
                self._alias_index[proc.lower()] = target_node
                self._tool_to_vertex[proc.lower()] = target_node

            # 3. Extract CLI flags and parameters: e.g. --db vfdb, --min_identity 90, --coverage 80
            flag_matches = re.findall(r'--?([a-zA-Z0-9_-]+)\s+([a-zA-Z0-9_-]+)', code)
            for flag, val in flag_matches:
                if len(flag) > 2:
                    self._alias_index[f"{flag.lower()}_{val.lower()}"] = target_node
                    if len(val) > 2:
                        self._alias_index[val.lower()] = target_node

            # 4. Extract tokens from comments and documentation
            comment_matches = re.findall(r'//\s*(.*)', code)
            for comment in comment_matches:
                words = re.findall(r'\b[a-zA-Z]{4,}\b', comment.lower())
                for w in words:
                    if w not in ("workflow", "process", "include", "return", "input", "output"):
                        if w not in self._alias_index:
                            self._alias_index[w] = target_node

            # 5. Extract take/emit channel signatures
            take_match = re.search(r'take:\s*([a-zA-Z0-9_,\s]+)(?=\s*main:|\s*emit:|\})', code)
            if take_match:
                for t in take_match.group(1).split():
                    t_clean = t.strip()
                    if t_clean and self.G.has_node(target_node):
                        norm_t = self.normalize_channel(t_clean)
                        self.component_takes[target_node].add(norm_t)
                        self.channel_consumed_by[norm_t].add(target_node)

            emit_match = re.search(r'emit:\s*([a-zA-Z0-9_,\s]+)(?=\s*\})', code)
            if emit_match:
                for e in emit_match.group(1).split():
                    e_clean = e.strip()
                    if e_clean and self.G.has_node(target_node):
                        norm_e = self.normalize_channel(e_clean)
                        self.component_emits[target_node].add(norm_e)
                        self.channel_produced_by[norm_e].add(target_node)

    def expand_composite_components(self, component_ids: List[str], store: Any = None) -> List[str]:
        """Recursively expand composite workflow/template nodes into their constituent atomic process steps.
        If an item in component_ids is a composite module, this traverses its AST includes and subworkflow definitions,
        replacing or augmenting the module with its underlying atomic process steps.
        Preserves original sequence order and deduplicates.
        """
        if not component_ids:
            return []

        expanded: List[str] = []
        seen: Set[str] = set()

        def _resolve_module_subcomponents(mod_id: str) -> List[str]:
            sub_steps = []
            # 1. Check graph edges (includes relation)
            if self.G.has_node(mod_id):
                for neighbor in self.G.neighbors(mod_id):
                    edge_data = self.G.get_edge_data(mod_id, neighbor, {})
                    if edge_data.get("relation") in ("includes", "subworkflow_of", "contains"):
                        if neighbor.startswith("step_") or neighbor.startswith("multi_"):
                            sub_steps.append(neighbor)

            # 2. Check code store AST dynamically
            code = ""
            if store:
                try:
                    code_item = store.get(("code",), mod_id)
                    if code_item and code_item.value:
                        code = code_item.value.get("content", "") if isinstance(code_item.value, dict) else str(code_item.value or "")
                except Exception:
                    pass
            if not code:
                from core.loader import data_loader
                code = getattr(data_loader, "code_db", {}).get(mod_id, "")

            if code and isinstance(code, str):
                include_matches = re.findall(r'include\s*\{\s*([a-zA-Z0-9_,\s;]+)\s*\}\s*from', code)
                for inc in include_matches:
                    for part in re.split(r'[,;]', inc):
                        clean_part = part.strip()
                        if clean_part.startswith("step_") or clean_part.startswith("multi_") or clean_part.startswith("module_"):
                            if clean_part.startswith("module_") and clean_part != mod_id:
                                sub_steps.extend(_resolve_module_subcomponents(clean_part))
                            else:
                                sub_steps.append(clean_part)

                proc_calls = re.findall(r'\b(step_[a-zA-Z0-9_]+|multi_[a-zA-Z0-9_]+)\s*\(', code)
                for p in proc_calls:
                    if p not in sub_steps:
                        sub_steps.append(p)

            # 3. Check template metadata components_used if in store
            if store:
                try:
                    tmpl_item = store.get(("templates",), mod_id)
                    if tmpl_item and tmpl_item.value:
                        for cu in tmpl_item.value.get("components_used", []):
                            if cu not in sub_steps:
                                sub_steps.append(cu)
                except Exception:
                    pass

            return sub_steps

        for cid in component_ids:
            cid_clean = str(cid).strip()
            if not cid_clean:
                continue

            # If it's a composite module or template, expand into its atomic steps
            if cid_clean.startswith("module_") or cid_clean.startswith("template_"):
                subs = _resolve_module_subcomponents(cid_clean)
                if subs:
                    for s in subs:
                        if s not in seen:
                            seen.add(s)
                            expanded.append(s)
                    continue

            # Default: keep the component itself
            if cid_clean not in seen:
                seen.add(cid_clean)
                expanded.append(cid_clean)

        return expanded

    def decompose_conjunction_query(self, query: str) -> List[str]:
        """Decompose a multi-tool conjunction or multi-clause scientific workflow query
        into constituent sub-queries dynamically.
        """
        if not query or not isinstance(query, str):
            return []

        import re
        q = query.strip()
        sub_queries = []

        # 1. Pattern: "both X and Y"
        both_match = re.search(r'\bboth\s+(.+?)\s+and\s+(.+?)(?:[\.,;\?]|$|\s+to\b|\s+for\b|\s+passing\b)', q, re.IGNORECASE)
        if both_match:
            sub1, sub2 = both_match.group(1).strip(), both_match.group(2).strip()
            sub_queries.extend([sub1, sub2])

        # 2. Pattern: "simultaneously through X and Y"
        simul_match = re.search(r'simultaneously\s+through\s+(.+?)\s+and\s+(.+?)(?:[\.,;\?]|$|\s+passing\b|\s+to\b)', q, re.IGNORECASE)
        if simul_match:
            sub1, sub2 = simul_match.group(1).strip(), simul_match.group(2).strip()
            if sub1 not in sub_queries:
                sub_queries.extend([sub1, sub2])

        # 3. Multi-clause scientific pipeline decomposition (split by commas, colons, semicolons, and sequential conjunctions)
        clauses = re.split(r'[:;,]|\band\s+finally\b|\band\s+also\b|\band\s+then\b|\band\s+filter\b|\b(?:\bfirst\b|\bthen\b|\bnext\b|\bfinally\b)\b', q, flags=re.IGNORECASE)
        for clause in clauses:
            clean_c = clause.strip().rstrip('.')
            if len(clean_c) > 5 and not any(clean_c.lower().startswith(p) for p in ("i have", "i need", "please", "can you", "workflow")):
                if ("two different databases" in clean_c.lower() or "two databases" in clean_c.lower() or "multiple databases" in clean_c.lower()) and self.G:
                    # Dynamically discover multiple matching screening vertices from active catalog
                    matching_nodes = [
                        n for n in self.G.nodes 
                        if any(w in str(self.G.nodes[n].get('label', '')).lower() or w in str(self.G.nodes[n].get('description', '')).lower() for w in ("amr", "resistance", "screen", "database"))
                        and self.G.nodes[n].get('type') == 'component'
                    ]
                    if len(matching_nodes) >= 2:
                        for m_node in matching_nodes[:2]:
                            sub_queries.append(f"{clean_c} {m_node}")
                    else:
                        sub_queries.append(clean_c)
                elif clean_c not in sub_queries:
                    sub_queries.append(clean_c)

        # Fallback to standard and-splitting if no clauses found
        if not sub_queries and (" and " in q or ", " in q):
            parts = re.split(r',\s*and\s+|\s+and\s+|,\s*', q)
            filtered = [p.strip() for p in parts if len(p.strip()) > 3]
            if len(filtered) >= 2:
                sub_queries = filtered

        return sub_queries if sub_queries else [q]

    # ─────────────────────────────────────────────────────────────────────────
    # Public Graphify Query API
    # ─────────────────────────────────────────────────────────────────────────

    def score_nodes(self, terms: List[str]) -> List[Tuple[float, str]]:
        """Compute ranked matching nodes for query terms."""
        return _score_query(self.G, terms, collect_per_term_seeds=False).ranked

    def query_graph(
        self,
        question: str,
        mode: str = "bfs",
        depth: int = 2,
        token_budget: int = 2000,
        context_filters: list[str] | None = None,
    ) -> str:
        """
        Execute Graphify question-answering search over the catalog knowledge graph.

        Args:
            question: Natural language query (e.g. 'preprocess raw data and map against reference')
            mode: 'bfs' for broad context, 'dfs' for tracing a specific sequence
            depth: Traversal depth (1 to 3, default 2)
            token_budget: Maximum tokens in formatted response
            context_filters: Optional list of confidence filters ['EXTRACTED', 'INFERRED']
        """
        if not self.is_built:
            return json.dumps({"error": "Knowledge graph not yet built."})

        terms = _query_terms(question)
        if not terms:
            return json.dumps({"error": "Query too short or only contains stopwords."})

        qs = _score_query(self.G, terms, collect_per_term_seeds=True)
        best_seed_by_term = qs.best_seed_by_term
        intent = {t for t in best_seed_by_term if t in _RELATIONAL_INTENT_TERMS}
        if intent and any(t not in _RELATIONAL_INTENT_TERMS for t in terms):
            best_seed_by_term = {t: nid for t, nid in best_seed_by_term.items() if t not in intent}

        start_nodes = _pick_seeds(qs.ranked, max_k=4, G=self.G, best_seed_by_term=best_seed_by_term)
        if not start_nodes:
            return json.dumps({"message": "No matching components found in knowledge graph.", "query": question})

        # Apply context/confidence filter if requested
        traversal_graph = self.G
        if context_filters:
            traversal_graph = nx.DiGraph()
            traversal_graph.add_nodes_from(self.G.nodes(data=True))
            allowed = set(c.upper() for c in context_filters)
            for u, v, d in self.G.edges(data=True):
                if d.get("confidence") in allowed or d.get("relation") in context_filters:
                    traversal_graph.add_edge(u, v, **d)

        depth = min(max(1, depth), 3)
        if mode == "dfs":
            nodes, edges = _dfs(traversal_graph, start_nodes, depth)
        else:
            nodes, edges = _bfs(traversal_graph, start_nodes, depth)

        header = f"GRAPH QUERY: '{question}' | Mode: {mode.upper()} | Seeds: {start_nodes} | {len(nodes)} nodes found\n\n"
        return header + _subgraph_to_text(traversal_graph, nodes, edges, token_budget, seeds=start_nodes)

    def explain_node(self, label: str) -> str:
        """Get full structural details, metadata, and all connected in/out edges for a component."""
        if not self.is_built:
            return json.dumps({"error": "Knowledge graph not yet built."})

        # Find best matching node
        terms = [label.lower()]
        scored = self.score_nodes(terms)
        if not scored:
            return json.dumps({"error": f"No node matching '{label}' found in catalog."})

        nid = scored[0][1]
        d = self.G.nodes[nid]

        lines = [
            f"COMPONENT: {nid}",
            f"  Tool: {d.get('tool', 'unknown')}",
            f"  Domain: {d.get('domain', 'unknown')}",
            f"  Description: {d.get('description', '')}",
            f"  Input Channels: {', '.join(d.get('inputs', [])) or '(none)'}",
            f"  Output Channels: {', '.join(d.get('outputs', [])) or '(none)'}",
            f"  Community: {d.get('community', 'unknown')}",
            f"  Total Degree: {self.G.degree(nid)} (In: {self.G.in_degree(nid)}, Out: {self.G.out_degree(nid)})",
            "",
            "UPSTREAM (Feeds into this component):",
        ]

        in_count = 0
        for pred in self.G.predecessors(nid):
            edata = self.G[pred][nid]
            ch = edata.get("channel", "")
            conf = edata.get("confidence", "AMBIGUOUS")
            rel = edata.get("relation", "dataflow")
            ch_str = f" via '{ch}'" if ch else ""
            lines.append(f"  <-- {pred} [{conf}: {rel}{ch_str}]")
            in_count += 1
        if in_count == 0:
            lines.append("  (No upstream components in catalog)")

        lines.append("\nDOWNSTREAM (Receives data from this component):")
        out_count = 0
        for succ in self.G.successors(nid):
            edata = self.G[nid][succ]
            ch = edata.get("channel", "")
            conf = edata.get("confidence", "AMBIGUOUS")
            rel = edata.get("relation", "dataflow")
            ch_str = f" via '{ch}'" if ch else ""
            lines.append(f"  --> {succ} [{conf}: {rel}{ch_str}]")
            out_count += 1
        if out_count == 0:
            lines.append("  (No downstream components in catalog)")

        return "\n".join(lines)

    def get_neighbors(
        self,
        label: str,
        direction: str = "both",
        relation_filter: str | None = None,
        token_budget: int = 2000,
    ) -> str:
        """Get direct upstream/downstream neighbors with edge details."""
        if not self.is_built:
            return json.dumps({"error": "Knowledge graph not yet built."})

        scored = self.score_nodes([label.lower()])
        if not scored:
            return json.dumps({"error": f"No node matching '{label}' found."})

        nid = scored[0][1]
        lines = [f"Neighbors of {nid}:"]

        if direction in ("out", "both"):
            for succ in self.G.successors(nid):
                edata = self.G[nid][succ]
                rel = edata.get("relation", "dataflow")
                conf = edata.get("confidence", "AMBIGUOUS")
                ch = edata.get("channel", "")
                if relation_filter and relation_filter.lower() not in rel.lower():
                    continue
                ch_str = f" via '{ch}'" if ch else ""
                lines.append(f"  --> {succ} [{conf}: {rel}{ch_str}]")

        if direction in ("in", "both"):
            for pred in self.G.predecessors(nid):
                edata = self.G[pred][nid]
                rel = edata.get("relation", "dataflow")
                conf = edata.get("confidence", "AMBIGUOUS")
                ch = edata.get("channel", "")
                if relation_filter and relation_filter.lower() not in rel.lower():
                    continue
                ch_str = f" via '{ch}'" if ch else ""
                lines.append(f"  <-- {pred} [{conf}: {rel}{ch_str}]")

        return "\n".join(lines)

    def get_community(self, community_id: int, token_budget: int = 2000) -> str:
        """List all catalog components in a functional community / subworkflow cluster."""
        if not self.is_built:
            return json.dumps({"error": "Knowledge graph not yet built."})

        nodes = self.communities.get(int(community_id), [])
        if not nodes:
            return json.dumps({"error": f"Community {community_id} not found."})

        lines = [f"Community {community_id} ({len(nodes)} components):"]
        for n in sorted(nodes):
            d = self.G.nodes[n]
            tool = d.get("tool", "")
            desc = d.get("description", "")[:100]
            lines.append(f"  - {n} [tool={tool}] — {desc}")

        return "\n".join(lines)

    def get_god_nodes(self, top_n: int = 10) -> str:
        """Return the top most-connected architectural hubs in the catalog."""
        if not self.is_built:
            return json.dumps({"error": "Knowledge graph not yet built."})

        ranked = sorted(
            [{"id": n, "label": self.G.nodes[n].get("label", n), "degree": self.G.degree(n),
              "tool": self.G.nodes[n].get("tool", "")} for n in self.G.nodes()],
            key=lambda x: -x["degree"]
        )[:top_n]

        lines = ["Catalog Architectural Hubs (God Nodes):"]
        for i, item in enumerate(ranked, 1):
            tool_str = f" (tool: {item['tool']})" if item['tool'] else ""
            lines.append(f"  {i}. {item['id']}{tool_str} — {item['degree']} connections")

        return "\n".join(lines)

    def graph_stats(self) -> str:
        """Return summary statistics of the knowledge graph."""
        if not self.is_built:
            return json.dumps({"error": "Knowledge graph not yet built."})

        confs = [d.get("confidence", "AMBIGUOUS") for _, _, d in self.G.edges(data=True)]
        total = len(confs) or 1
        n_ext = confs.count("EXTRACTED")
        n_inf = confs.count("INFERRED")
        n_amb = confs.count("AMBIGUOUS")

        return (
            f"Nodes: {self.G.number_of_nodes()}\n"
            f"Edges: {self.G.number_of_edges()}\n"
            f"Communities: {len(self.communities)}\n"
            f"EXTRACTED: {round(n_ext / total * 100)}% ({n_ext})\n"
            f"INFERRED: {round(n_inf / total * 100)}% ({n_inf})\n"
            f"AMBIGUOUS: {round(n_amb / total * 100)}% ({n_amb})"
        )

    def find_path_detailed(
        self,
        source: str,
        target: str,
        directed: bool = True,
        max_hops: int = 8,
    ) -> str:
        """Find dataflow path between two components with ambiguity detection and step annotations."""
        if not self.is_built:
            return json.dumps({"error": "Knowledge graph not yet built."})

        src_scored = self.score_nodes([source.lower()])
        tgt_scored = self.score_nodes([target.lower()])

        if not src_scored:
            return json.dumps({"error": f"Source '{source}' not found in catalog."})
        if not tgt_scored:
            return json.dumps({"error": f"Target '{target}' not found in catalog."})

        src_id = src_scored[0][1]
        tgt_id = tgt_scored[0][1]

        if src_id == tgt_id:
            return json.dumps({
                "path": [src_id],
                "hops": 0,
                "note": f"Source and target both resolved to same component '{src_id}'.",
            })

        graph_to_search = self.G if directed else self.G.to_undirected()
        try:
            path = nx.shortest_path(
                graph_to_search,
                src_id,
                tgt_id,
                weight=lambda u, v, d: 1.0 / (d.get("weight", 1) + 0.01),
            )
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            if directed:
                # Try reversed direction to see if reversed flow exists
                try:
                    rev_path = nx.shortest_path(self.G, tgt_id, src_id)
                    return json.dumps({
                        "error": f"No forward dataflow from '{src_id}' to '{tgt_id}'.",
                        "reversed_path": rev_path,
                        "hint": f"Data flows in reverse: {tgt_id} -> ... -> {src_id}. Check pipeline stage ordering.",
                    }, indent=2)
                except Exception:
                    pass
            return json.dumps({
                "error": f"No dataflow path found between '{src_id}' and '{tgt_id}'.",
                "hint": "Components may operate on incompatible channel types or require an intermediate adapter.",
            }, indent=2)

        if len(path) - 1 > max_hops:
            return json.dumps({"error": f"Path exceeds max_hops={max_hops} ({len(path)-1} hops)."})

        steps = []
        for i in range(len(path) - 1):
            u, v = path[i], path[i + 1]
            if self.G.has_edge(u, v):
                edata = self.G[u][v]
                steps.append({
                    "from": u,
                    "to": v,
                    "direction": "-->",
                    "channel": edata.get("channel", ""),
                    "confidence": edata.get("confidence", "AMBIGUOUS"),
                    "relation": edata.get("relation", "dataflow"),
                })
            elif self.G.has_edge(v, u):
                edata = self.G[v][u]
                steps.append({
                    "from": u,
                    "to": v,
                    "direction": "<--",
                    "channel": edata.get("channel", ""),
                    "confidence": edata.get("confidence", "AMBIGUOUS"),
                    "relation": edata.get("relation", "dataflow"),
                })

        return json.dumps({
            "source": src_id,
            "target": tgt_id,
            "hops": len(path) - 1,
            "path": path,
            "steps": steps,
        }, indent=2)

    def export_graph_json(self, output_path: str = "graphify-out/catalog_graph.json") -> str:
        """Export the catalog graph in standard Graphify graph.json format."""
        if not self.is_built:
            raise RuntimeError("Knowledge graph must be built before exporting.")

        out_path = Path(output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        data = json_graph.node_link_data(self.G, edges="links")
        data["directed"] = True
        out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("exported_catalog_graph_json", path=str(out_path), nodes=self.G.number_of_nodes(), edges=self.G.number_of_edges())
        return str(out_path)

    # ─────────────────────────────────────────────────────────────────────────
    # Topological Vertex Projection & Closed-World Validation API
    # ─────────────────────────────────────────────────────────────────────────

    def project_vertex(self, query_str: str) -> Optional[str]:
        """Project an arbitrary query, shorthand name, or alias onto a canonical vertex v in V(G).
        Returns None if the query is strictly out-of-bounds (closed-world enforcement).
        """
        if not query_str or not isinstance(query_str, str):
            return None

        q_raw = query_str.strip()
        if not q_raw:
            return None

        # 1. Exact node match
        if q_raw in self.G:
            return q_raw

        q_lower = q_raw.lower()

        # 2. Plugin-configured query synonyms mapping
        if hasattr(self, "query_synonyms") and self.query_synonyms:
            if q_lower in self.query_synonyms:
                syn_target = self.query_synonyms[q_lower]
                if syn_target in self.G:
                    return syn_target

        # 3. Case-insensitive exact match
        for node in self.G.nodes():
            if node.lower() == q_lower:
                return node

        # 4. Exact tool match from inverted index
        if q_lower in self._tool_to_vertex:
            return self._tool_to_vertex[q_lower]

        # 4. Suffix / Substring tool match (e.g. "trimming__fastp" -> "step_1PP_trimming__fastp")
        if q_lower in self._alias_index:
            return self._alias_index[q_lower]

        # 5. Suffix match on node names (e.g., matching __<tool> or _<tool>)
        candidates = []
        for node in self.G.nodes():
            node_lower = node.lower()
            if node_lower.endswith(f"__{q_lower}") or node_lower.endswith(f"_{q_lower}"):
                candidates.append(node)
            elif f"__{q_lower}__" in node_lower or f"_{q_lower}_" in node_lower:
                candidates.append(node)

        if len(candidates) == 1:
            return candidates[0]
        elif len(candidates) > 1:
            # Prioritize candidate with highest word/token overlap with query
            q_words = set(re.split(r'[^a-z0-9]', q_lower))
            scored_candidates = []
            for c in candidates:
                c_parts = set(re.split(r'[^a-z0-9]', c.lower()))
                overlap = len(q_words & c_parts)
                scored_candidates.append((overlap, c))
            scored_candidates.sort(key=lambda x: x[0], reverse=True)
            if scored_candidates[0][0] > 0:
                return scored_candidates[0][1]
            exact_suffix = [c for c in candidates if c.lower().endswith(f"__{q_lower}")]
            if exact_suffix:
                return exact_suffix[0]
            return candidates[0]

        # 6. Exact single-token fuzzy match (only for short single words)
        if len(q_raw) < 25 and ' ' not in q_raw:
            import difflib
            matches = difflib.get_close_matches(q_raw, list(self.G.nodes()), n=1, cutoff=0.85)
            if matches:
                return matches[0]

        # 8. Direct Catalog Item Keyword Scoring
        catalog_items = []
        if hasattr(self, "store") and self.store:
            try:
                catalog_items = [c.value for c in self.store.search(("components",), limit=500)]
            except Exception:
                pass
        if not catalog_items:
            try:
                from core.loader import data_loader
                if data_loader and hasattr(data_loader, "catalog_db"):
                    catalog_items = list(data_loader.catalog_db.values())
            except Exception:
                pass

        if catalog_items:
            q_tokens = [w for w in re.split(r'[^a-z0-9]', q_lower) if len(w) > 2]
            best_score = 0
            best_id = None
            try:
                for data in catalog_items:
                    if not isinstance(data, dict): continue
                    c_id = data.get("id") or data.get("tool")
                    if not c_id: continue
                    score = 0
                    tool = str(data.get("tool", "")).lower()
                    if tool and tool in q_lower:
                        score += 60
                    for kw in (data.get("keywords") or []):
                        kw_str = str(kw).lower()
                        if kw_str in q_lower:
                            score += 50
                        for w in kw_str.split():
                            if w in q_tokens:
                                score += 25
                    suffix = c_id.split("__")[-1].lower() if "__" in c_id else c_id.lower()
                    if suffix in q_lower:
                        score += 50
                    desc = str(data.get("description", "")).lower()
                    for t in q_tokens:
                        if t in desc:
                            score += 15
                    if score > best_score and score >= 20:
                        best_score = score
                        best_id = c_id
                if best_id:
                    return best_id
            except Exception:
                pass

        return None

    def partition_raw_ids(self, raw_ids: List[str]) -> Tuple[List[str], List[str]]:
        """Bipartite partitioning of raw candidate tokens into:
        1. Valid canonical process/template components in V(G)
        2. Helper functions in F (e.g. 'extractKey', 'getFastqPair')
        Discards invalid/hallucinated tokens that do not belong to V or F.
        """
        valid_components: List[str] = []
        helper_funcs: List[str] = []
        seen_comps: Set[str] = set()

        for raw in raw_ids:
            if not raw or not isinstance(raw, str):
                continue
            r_clean = raw.strip()
            if not r_clean:
                continue

            # Check if token belongs to helper closure set F
            if r_clean in self._helper_closures or r_clean.lower() in {h.lower() for h in self._helper_closures}:
                if r_clean not in helper_funcs:
                    helper_funcs.append(r_clean)
                continue

            # Project onto vertex set V(G)
            v = self.project_vertex(r_clean)
            if v and v not in seen_comps:
                seen_comps.add(v)
                valid_components.append(v)

        return valid_components, helper_funcs

    def bridge_pipeline_path(self, component_ids: List[str]) -> List[str]:
        """Verify reachability between sequential pipeline components.
        If a gap exists (e.g. raw_reads -> lineage_typing), runs BFS shortest path
        on G to automatically insert missing bridge components.
        """
        if len(component_ids) <= 1 or not self.is_built:
            return list(component_ids)

        bridged: List[str] = [component_ids[0]]
        for i in range(len(component_ids) - 1):
            src, tgt = component_ids[i], component_ids[i + 1]
            if src == tgt:
                continue

            # Check if direct edge exists in G
            if self.G.has_edge(src, tgt):
                if tgt not in bridged:
                    bridged.append(tgt)
                continue

            # Try to find forward shortest path in G
            try:
                path = nx.shortest_path(self.G, src, tgt)
                for intermediate in path[1:]:
                    if intermediate not in bridged:
                        bridged.append(intermediate)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                if tgt not in bridged:
                    bridged.append(tgt)

        return bridged

    # ─────────────────────────────────────────────────────────────────────────
    # Backward-Compatible Public API
    # ─────────────────────────────────────────────────────────────────────────

    def build_graph(self, store: Any) -> None:
        self.build_nx_graph(store)

    def find_path(self, start_comp: str, end_comp: str, store: Any = None) -> List[str]:
        if not self.is_built and store:
            self.build_nx_graph(store)
        try:
            return nx.shortest_path(self.G, start_comp, end_comp)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return []

    def get_upstream_nodes(self, anchor: str, max_depth: int = 2, store: Any = None) -> List[Tuple[str, int]]:
        if not self.is_built and store:
            self.build_nx_graph(store)
        upstream = set()
        queue = [(anchor, 0)]
        visited = {anchor}
        while queue:
            current, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            for pred in self.G.predecessors(current):
                if pred not in visited:
                    visited.add(pred)
                    upstream.add((pred, depth + 1))
                    queue.append((pred, depth + 1))
        return sorted(list(upstream), key=lambda x: x[1])

    def get_downstream_nodes(self, anchor: str, max_depth: int = 2, store: Any = None) -> List[Tuple[str, int]]:
        if not self.is_built and store:
            self.build_nx_graph(store)
        downstream = set()
        queue = [(anchor, 0)]
        visited = {anchor}
        while queue:
            current, depth = queue.pop(0)
            if depth >= max_depth:
                continue
            for succ in self.G.successors(current):
                if succ not in visited:
                    visited.add(succ)
                    downstream.add((succ, depth + 1))
                    queue.append((succ, depth + 1))
        return sorted(list(downstream), key=lambda x: x[1])

    def get_related_components(self, component_id: str, store: Any = None, top_k: int = 5) -> List[Tuple[str, int]]:
        if not self.is_built and store:
            self.build_nx_graph(store)
        weights = self.usage_weights.get(component_id, {})
        return sorted(weights.items(), key=lambda x: x[1], reverse=True)[:top_k]

    def generate_architect_blueprint(self, component_ids: List[str], store: Any = None) -> str:
        """Generate a Graphify-inspired topological blueprint for the Architect LLM.
        Computes:
          1. Induced subgraph G[S]
          2. Topological ordering of process execution
          3. AST channel connection wiring between adjacent nodes
          4. Source inputs (in-degree 0) and terminal sink outputs (out-degree 0)
        """
        if not self.is_built and store:
            self.build_nx_graph(store)
        if not self.is_built or not component_ids:
            return ""

        import networkx as nx

        # 1. Project and canonicalize IDs
        valid_nodes = []
        for cid in component_ids:
            v = self.project_vertex(cid)
            if v and v in self.G and v not in valid_nodes:
                valid_nodes.append(v)

        if not valid_nodes:
            return ""

        # 2. Induced subgraph
        sub_g = self.G.subgraph(valid_nodes).copy()

        # 3. Topological sort on DAG (or degree sort if cycles exist)
        try:
            topo_order = list(nx.topological_sort(sub_g))
        except Exception:
            topo_order = sorted(valid_nodes, key=lambda n: (sub_g.in_degree(n), -sub_g.out_degree(n)))

        # 4. Sources and Sinks in induced subgraph
        sources = [n for n in valid_nodes if sub_g.in_degree(n) == 0]
        sinks = [n for n in valid_nodes if sub_g.out_degree(n) == 0]

        # 5. Channel dataflow wiring edges
        wiring_lines = []
        for u in topo_order:
            u_data = self.G.nodes.get(u, {})
            u_emits = u_data.get("output_channels") or u_data.get("out") or []
            successors = list(sub_g.successors(u))
            for v in successors:
                edge_data = sub_g.get_edge_data(u, v, {})
                channel_name = edge_data.get("channel") or (u_emits[0] if u_emits else "out")
                v_data = self.G.nodes.get(v, {})
                v_takes = v_data.get("input_channels") or v_data.get("input_types") or []
                wiring_lines.append(f"  * `{u}.out.{channel_name}` -> passes into `{v}` (takes: `{v_takes}`)")

        # 6. Format the blueprint markdown
        blueprint_parts = [
            "### 🧬 GRAPHIFY TOPOLOGICAL BLUEPRINT (Ground Truth DAG):",
            f"- **Execution Topological Order**: `{' -> '.join(topo_order)}`",
            f"- **Ingestion Sources (in-degree 0, instantiate in entrypoint)**: `{sources}`",
            f"- **Terminal Sinks (out-degree 0 / final results)**: `{sinks}`",
        ]

        if wiring_lines:
            blueprint_parts.append("- **Verified Dataflow Channel Wiring**:")
            blueprint_parts.extend(wiring_lines)
        else:
            blueprint_parts.append("- **Verified Dataflow Channel Wiring**: Sequential pipeline flow")

        return "\n".join(blueprint_parts)

    def detect_collection_cardinality(self, component_id: str, store: Any = None) -> List[Dict[str, Any]]:
        """Agnostically inspect process AST input declarations for glob patterns (path '*'),
        collection/list declarations, or collection metadata to determine if inputs must be .collect().
        """
        import re
        code = ""
        if store:
            try:
                code_item = store.get(("code",), component_id)
                if code_item and code_item.value:
                    code = code_item.value.get("content", "") if isinstance(code_item.value, dict) else str(code_item.value or "")
            except Exception:
                pass
        if not code:
            from core.loader import data_loader
            code = getattr(data_loader, "code_db", {}).get(component_id, "")

        collect_directives = []
        if code and isinstance(code, str):
            input_block_match = re.search(r'input:\s*([\s\S]*?)(?=output:|script:|exec:|when:|\})', code)
            if input_block_match:
                input_lines = input_block_match.group(1).split("\n")
                for line in input_lines:
                    line_clean = line.strip()
                    if not line_clean or line_clean.startswith("//"):
                        continue
                    if re.search(r'path\s*\(?[\'"][^\'"]*\*+[^\'"]*[\'"]\)?', line_clean) or "collect" in line_clean.lower() or "alleles" in line_clean.lower():
                        param_name_match = re.search(r'path\s*\(?\s*([a-zA-Z0-9_]+)\s*\)?', line_clean)
                        param_name = param_name_match.group(1) if param_name_match else "input_files"
                        collect_directives.append({
                            "component": component_id,
                            "param": param_name,
                            "reason": f"AST input declaration '{line_clean}' requires a multi-file collection.",
                            "idiom": f"all_{param_name} = upstream_channel.collect()"
                        })
        return collect_directives

    def detect_fan_in_mix(self, selected_components: List[str], store: Any = None) -> List[Dict[str, Any]]:
        """Dynamically detect DAG in-degree >= 2 where multiple parallel producers emit compatible channels
        that feed into a common consumer channel, prescribing .mix().
        """
        if len(selected_components) < 3:
            return []

        fan_in_directives = []
        from collections import defaultdict
        channel_producers = defaultdict(list)

        for comp in selected_components:
            emits = self.component_emits.get(comp, set())
            for ch in emits:
                if ch and ch not in ("none", "void", "val", "unknown"):
                    channel_producers[ch].append(comp)

        for ch, producers in channel_producers.items():
            if len(producers) >= 2:
                consumers = [c for c in selected_components if ch in self.component_takes.get(c, set()) and c not in producers]
                if consumers:
                    for cons in consumers:
                        prod_exprs = [f"{p}.out.{ch}" for p in producers]
                        mix_expr = f"{prod_exprs[0]}.mix({', '.join(prod_exprs[1:])})"
                        fan_in_directives.append({
                            "channel": ch,
                            "producers": producers,
                            "consumer": cons,
                            "idiom": f"unified_{ch} = {mix_expr}\n{cons}(unified_{ch})"
                        })
        return fan_in_directives

    def detect_cross_multimap_routing(self, selected_components: List[str], store: Any = None) -> List[Dict[str, Any]]:
        """Dynamically detect when a consumer process requires inputs from two parallel asynchronous
        upstream producer streams that share a sample key, deducing .cross(...) { extractKey(it) }
        and .multiMap { ... } purely from graph topology and channel definitions.
        """
        cross_directives = []
        if len(selected_components) < 3 or not self.is_built:
            return cross_directives

        # Map each channel to its producers in selected_components
        producers_by_channel: Dict[str, List[str]] = {}
        for comp in selected_components:
            emits = self.component_emits.get(comp, set())
            for ch in emits:
                if ch and ch not in ("none", "void", "val", "unknown"):
                    producers_by_channel.setdefault(ch, []).append(comp)

        # Detect consumers taking inputs from parallel upstream branches
        joined_pairs_seen = set()
        for consumer in selected_components:
            takes = list(self.component_takes.get(consumer, []))
            if len(takes) < 2:
                continue

            # Find active producers for these take channels
            active_producers = []
            for t_ch in takes:
                prods = [p for p in producers_by_channel.get(t_ch, []) if p != consumer]
                if prods:
                    active_producers.append((t_ch, prods[0]))

            # If consumer takes from 2+ distinct upstream producers
            if len(active_producers) >= 2:
                for i in range(len(active_producers) - 1):
                    ch1, p1 = active_producers[i]
                    ch2, p2 = active_producers[i + 1]
                    if p1 == p2:
                        continue

                    # Check that neither producer is an ancestor of the other (parallel streams)
                    p1_to_p2 = nx.has_path(self.G, p1, p2) if (p1 in self.G and p2 in self.G) else False
                    p2_to_p1 = nx.has_path(self.G, p2, p1) if (p1 in self.G and p2 in self.G) else False

                    # If parallel or one is an annotation branch (e.g. classifier output)
                    pair_key = tuple(sorted([p1, p2]))
                    if pair_key not in joined_pairs_seen:
                        joined_pairs_seen.add(pair_key)
                        var_name1 = ch1 if ch1 else "stream1"
                        var_name2 = ch2 if ch2 else "stream2"
                        joined_var = f"{var_name1}And{var_name2.capitalize()}"

                        cross_directives.append({
                            "producer_data": p1,
                            "producer_species": p2,
                            "channel1": ch1,
                            "channel2": ch2,
                            "consumer": consumer,
                            "consumers": [consumer],
                            "stream_name": var_name1,
                            "joined_var": joined_var,
                            "idiom": (
                                f"{var_name1}.cross({var_name2})\n"
                                f"        .multiMap {{\n"
                                f"            {var_name1}: it[0]\n"
                                f"            {var_name2}: it[1][1]\n"
                                f"        }}.set {{ {joined_var} }}"
                            )
                        })
                    else:
                        for cd in cross_directives:
                            if cd.get("producer_data") in (p1, p2) and cd.get("producer_species") in (p1, p2):
                                if consumer not in cd["consumers"]:
                                    cd["consumers"].append(consumer)
                                break

        return cross_directives

    def deduce_tuple_arity_projection(self, producer_id: str, consumer_id: str, store: Any = None) -> Optional[Dict[str, Any]]:
        """Dynamically compare emitted tuple dimension N vs consumed tuple dimension M
        and compute the dimension projection lambda .map { it -> [...] }.
        Works purely through component channel metadata.
        """
        p_emits = list(self.component_emits.get(producer_id, []))
        c_takes = list(self.component_takes.get(consumer_id, []))

        if not p_emits or not c_takes:
            return None

        if len(p_emits) > len(c_takes) and len(c_takes) >= 1:
            vars_in = [f"f{i}" for i in range(len(p_emits))]
            vars_out = vars_in[:len(c_takes)]
            return {
                "producer": producer_id,
                "consumer": consumer_id,
                "emitted_arity": len(p_emits),
                "taken_arity": len(c_takes),
                "idiom": f"{producer_id}.out.{p_emits[0]}.map {{ {', '.join(vars_in)} -> [ {', '.join(vars_out)} ] }}"
            }
        elif len(c_takes) > len(p_emits) and len(p_emits) >= 2:
            # Generic channel arity expansion with default arguments
            padding = ["'-'"] * (len(c_takes) - len(p_emits))
            return {
                "producer": producer_id,
                "consumer": consumer_id,
                "emitted_arity": len(p_emits),
                "taken_arity": len(c_takes),
                "idiom": f"{producer_id}.out.{p_emits[0]}.map {{ [ it[0], it[1], {', '.join(padding)} ] }}"
            }
        return None

    def synthesize_dataflow_directives(self, selected_components: List[str], store: Any = None) -> List[str]:
        """Synthesize all dynamic operator directives (.collect, .mix, .map, .cross, .join, .multiMap)
        for a given set of components without hardcoding tool names or prefixes.
        """
        directives = []

        # 1. Cross-join + multiMap species routing
        cross_info = self.detect_cross_multimap_routing(selected_components, store=store)
        for item in cross_info:
            consumers_list = item.get("consumers", [item.get("consumer", "")])
            directives.append(
                f"SPECIES-AWARE ROUTING DIRECTIVE (.cross + .multiMap):\n"
                f"- Downstream typing processes ({', '.join([c for c in consumers_list if c])}) require both {item['stream_name']} and species identification.\n"
                f"- You MUST join streams on sample key and split into named outputs before calling downstream processes:\n"
                f"```groovy\n{item['idiom']}\n```"
            )

        # 2. Collection / Aggregation (.collect)
        for comp in selected_components:
            coll_info = self.detect_collection_cardinality(comp, store=store)
            for item in coll_info:
                directives.append(
                    f"CARDINALITY DIRECTIVE for `{item['component']}`:\n"
                    f"- {item['reason']}\n"
                    f"- You MUST aggregate upstream items using `.collect()` (e.g. `{item['idiom']}`)."
                )

        # 3. Fan-in stream merging (.mix)
        mix_info = self.detect_fan_in_mix(selected_components, store=store)
        for item in mix_info:
            directives.append(
                f"FAN-IN MERGE DIRECTIVE for `{item['consumer']}`:\n"
                f"- Multiple parallel producers ({', '.join(item['producers'])}) emit channel `{item['channel']}`.\n"
                f"- Merge streams before calling `{item['consumer']}`:\n```groovy\n{item['idiom']}\n```"
            )

        # 4. Tuple arity reshaping (.map)
        for i in range(len(selected_components) - 1):
            p = selected_components[i]
            c = selected_components[i + 1]
            proj = self.deduce_tuple_arity_projection(p, c, store=store)
            if proj:
                directives.append(
                    f"TUPLE RESHAPING DIRECTIVE `{p}` → `{c}`:\n"
                    f"- Producer emits arity {proj['emitted_arity']}, Consumer takes arity {proj['taken_arity']}.\n"
                    f"- Reshape channel:\n```groovy\n{proj['idiom']}\n```"
                )

        return directives


# Global singleton
kg = KnowledgeGraph()


if __name__ == "__main__":
    import argparse
    from langgraph.store.memory import InMemoryStore
    from core.loader import data_loader

    parser = argparse.ArgumentParser(description="Graphify Catalog Knowledge Graph CLI")
    subparsers = parser.add_subparsers(dest="cmd")

    query_p = subparsers.add_parser("query", help="Query the knowledge graph")
    query_p.add_argument("question", type=str)
    query_p.add_argument("--mode", type=str, default="bfs", choices=["bfs", "dfs"])
    query_p.add_argument("--depth", type=int, default=2)
    query_p.add_argument("--budget", type=int, default=2000)

    explain_p = subparsers.add_parser("explain", help="Explain a specific component")
    explain_p.add_argument("label", type=str)

    path_p = subparsers.add_parser("path", help="Find path between two components")
    path_p.add_argument("source", type=str)
    path_p.add_argument("target", type=str)
    path_p.add_argument("--undirected", action="store_true")

    subparsers.add_parser("god-nodes", help="List architectural hubs")
    subparsers.add_parser("stats", help="Show knowledge graph statistics")
    subparsers.add_parser("export", help="Export graphify graph.json")

    args = parser.parse_args()
    if not args.cmd:
        parser.print_help()
        sys.exit(0)

    store = InMemoryStore()
    data_loader.load_all(store)
    from core.services.knowledge_graph import kg as target_kg
    if not target_kg.is_built:
        target_kg.build_nx_graph(store)

    if args.cmd == "query":
        print(target_kg.query_graph(args.question, mode=args.mode, depth=args.depth, token_budget=args.budget))
    elif args.cmd == "explain":
        print(target_kg.explain_node(args.label))
    elif args.cmd == "path":
        print(target_kg.find_path_detailed(args.source, args.target, directed=not args.undirected))
    elif args.cmd == "god-nodes":
        print(target_kg.get_god_nodes())
    elif args.cmd == "stats":
        print(target_kg.graph_stats())
    elif args.cmd == "export":
        out = target_kg.export_graph_json()
        print(f"Exported to {out}")


