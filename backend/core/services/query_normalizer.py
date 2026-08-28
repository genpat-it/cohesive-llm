import re

IGNORE_WORDS = {
    "step", "module", "tool",
    "pipeline", "workflow", "build", "create", "make", "run", "using",
    "file", "data", "generate", "process",
    "custom", "script", "and", "plus", "with",
    # Stop words
    "a", "an", "the", "or", "but", "in", "on", "at", "to", "for", "of", "by",
    "is", "are", "was", "were", "be", "been", "being", "it", "this", "that", "these", "those",
    "we", "you", "they", "who", "which", "where", "when", "why", "how",
    "do", "does", "did", "can", "could", "should", "would", "may", "might", "must",
    "have", "has", "had", "not", "no", "yes", "please", "just", "from", "about"
}

DISCOVERY_PHRASES = [
    "what can i", "what can you do", "what do you have", "what do we have",
    "what tools", "which tools", "what pipelines", "what modules",
    "what's supported", "what is supported", "available options",
    "available tools", "available pipelines", "system capabilities",
    "show me everything", "list everything", "what components",
    "what steps", "tell me about", "what analyses", "what is available",
    "supported analyses", "supported workflows", "what workflows",
    "give me an overview", "show all", "list all",
    "capabilities", "functionality", "feature list",
]

ACTION_WORDS = {
    "suggest", "list", "show", "recommend", "overview", "catalog",
    "options", "give", "help", "describe", "what", "display", "browse",
    "explore", "summarize", "enumerate",
}

TARGET_NOUNS = {
    "tool", "tools", "pipeline", "pipelines", "module", "modules",
    "capability", "capabilities", "system", "component", "components",
    "step", "steps", "workflow", "workflows", "analysis", "analyses",
}

PUNCT_RE = re.compile(r"[^\w\s\_]", re.IGNORECASE)
WORD_RE = re.compile(r"\b\w+\b", re.IGNORECASE)
FILLER_RE = re.compile(
    r"\b(please|help|need|want|looking|build|design|create|make|"
    r"pipeline|that|performs|does|can|you|"
    r"would|like|could|should|also|just|really|actually|basically|"
    r"i|me|my|give|write|develop|implement|set up|configure)\b",
    re.IGNORECASE,
)


def _expand_tokens(base_tokens: set[str]) -> set[str]:  # noqa: C901
    query_tokens = set(base_tokens)
    for token in base_tokens:
        if len(token) > 4:
            if token.endswith("ing"):
                query_tokens.add(token[:-3])
            if token.endswith("ed"):
                query_tokens.add(token[:-2])
            if token.endswith("ies"):
                query_tokens.add(token[:-3] + "y")
            if token.endswith("ation"):
                query_tokens.add(token[:-5] + "e")
            if token.endswith("er"):
                query_tokens.add(token[:-2])
            if token.endswith("ment"):
                query_tokens.add(token[:-4])
                query_tokens.add(token[:-4] + "e")
            if token.endswith("ness"):
                query_tokens.add(token[:-4])
            if token.endswith("ous"):
                query_tokens.add(token[:-3])
            if token.endswith("ive"):
                query_tokens.add(token[:-3] + "e")
        if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
            query_tokens.add(token[:-1])
    return query_tokens


def _expand_synonyms(query_tokens: set[str]) -> set[str]:
    expanded = set(query_tokens)
    try:
        from core.plugin_loader import get_active_plugin
        plugin = get_active_plugin()
        domain_synonyms = plugin.rag_setting("query_synonyms", {})
    except Exception:
        domain_synonyms = {}

    for base, syns in domain_synonyms.items():
        if base in query_tokens or any(s in query_tokens for s in syns):
            expanded.add(base)
            expanded.update(syns)
    return expanded


def normalize_query(user_query: str) -> dict[str, set[str] | str]:
    query_lower = (user_query or "").lower()

    try:
        from core.plugin_loader import get_active_plugin
        plugin = get_active_plugin()
        domain_replacements = plugin.rag_setting("query_replacements", {})
    except Exception:
        domain_replacements = {}

    for old, new in domain_replacements.items():
        query_lower = query_lower.replace(old, new)

    query_lower = PUNCT_RE.sub(" ", query_lower)
    clean_query = query_lower.strip()

    dense = FILLER_RE.sub(" ", clean_query)
    base_tokens = set()
    for t in WORD_RE.findall(dense):
        if t not in IGNORE_WORDS and t not in ACTION_WORDS and t not in TARGET_NOUNS:
            base_tokens.add(t)

    query_tokens = _expand_synonyms(_expand_tokens(base_tokens))
    return {
        "query_lower": query_lower,
        "clean_query": clean_query,
        "query_tokens": query_tokens,
    }


def is_discovery_query(clean_query: str) -> bool:
    if not clean_query:
        return True

    # If the query is very short but it's a specific term (not an action/target word), don't block it.
    if len(clean_query) < 15:
        tokens = set(WORD_RE.findall(clean_query))
        if any(t in ACTION_WORDS or t in TARGET_NOUNS or t in IGNORE_WORDS for t in tokens):
            return True
        if len(tokens) == 1:
            return False  # allow single-word acronym searches
        return True

    if any(phrase in clean_query for phrase in DISCOVERY_PHRASES):
        return True

    # Pre-split tokens for faster evaluation
    tokens = set(WORD_RE.findall(clean_query))
    has_action = any(t in ACTION_WORDS for t in tokens)
    has_target = any(t in TARGET_NOUNS for t in tokens)
    return bool(has_action and has_target)


def build_semantic_query(clean_query: str, query_tokens: set[str]) -> str:
    dense_query = FILLER_RE.sub(" ", clean_query)

    words_to_strip = IGNORE_WORDS | ACTION_WORDS | TARGET_NOUNS

    dense_words = []
    for w in WORD_RE.findall(dense_query):
        if w not in words_to_strip:
            dense_words.append(w)

    dense_query_str = " ".join(dense_words)

    expanded_terms = [word for word in query_tokens if word not in dense_query_str]
    semantic_query = (dense_query_str + " " + " ".join(expanded_terms)).strip()

    if len(semantic_query.replace(" ", "")) < 3:
        return clean_query
    return semantic_query
