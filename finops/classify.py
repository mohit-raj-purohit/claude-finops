"""Heuristic prompt classification.

Deliberately transparent and rule-based: every prompt records WHY it landed in a
category so the dashboard can show the evidence rather than an opaque label.
"""
import re

CATEGORIES = [
    ("debugging", 3.0, r"\b(bug|debug|error|exception|traceback|stack ?trace|not working|broken|fails?|failing|failed|crash|fix this|why (is|does|isn'?t)|troubleshoot|502|500|null pointer|undefined is not)\b"),
    ("code_review", 2.6, r"\b(review|code ?review|pr\b|pull request|feedback on|critique|lgtm|nitpick|approve)\b"),
    ("refactoring", 2.5, r"\b(refactor|clean ?up|simplify|restructure|rename|extract (a )?(function|method|component)|dedupe|deduplicate|tech debt|tidy)\b"),
    ("testing", 2.4, r"\b(test|tests|unit test|integration test|pytest|jest|coverage|assert|mock|spec file)\b"),
    ("documentation", 2.3, r"\b(document|documentation|docs?|readme|changelog|comment(s)? (for|on)|docstring|write up|write-up)\b"),
    ("architecture", 2.2, r"\b(architect|architecture|design (the|a) (system|schema|api)|system design|data model|schema|scal(e|ing|ability)|trade[- ]?offs?|high level design)\b"),
    ("planning", 2.1, r"\b(plan|roadmap|break (this )?down|steps to|approach|strategy|estimate|milestone|backlog|prioriti[sz]e)\b"),
    ("automation", 2.0, r"\b(script|automat|cron|pipeline|ci/?cd|workflow|deploy|jenkins|github action|makefile|bash script)\b"),
    ("research", 1.9, r"\b(research|compare|find out|look up|investigate|what (is|are)|explore options|alternatives|pros and cons|benchmark|which (library|tool|framework))\b"),
    ("learning", 1.8, r"\b(explain|how does|teach me|understand|what does .* mean|walk me through|help me learn|tutorial|eli5)\b"),
    ("writing", 1.7, r"\b(write (an?|the)? ?(email|blog|post|copy|article|summary|message|slide)|draft|rephrase|proofread|tone|paraphrase)\b"),
    ("data_analysis", 1.7, r"\b(analy[sz]e|dataset|csv|dataframe|sql query|aggregate|chart|visuali[sz]|report on|metrics)\b"),
    ("coding", 1.5, r"\b(implement|build|create|add|write (a )?(function|class|component|endpoint|module)|code|feature|api|component|migrate|integrate|install|setup|set up|configure)\b"),
    ("casual", 1.0, r"^\s*(hi|hey|hello|thanks|thank you|ok|okay|yes|no|cool|nice|great|continue|go ahead|proceed|yep|sure)\b"),
]

_COMPILED = [(name, w, re.compile(pat, re.I)) for name, w, pat in CATEGORIES]


def classify(text):
    """Return (category, confidence 0-1, matched_terms)."""
    if not text or not text.strip():
        return "other", 0.0, []
    head = text[:4000]
    scores = {}
    evidence = {}
    for name, weight, rx in _COMPILED:
        hits = rx.findall(head)
        if hits:
            flat = []
            for h in hits:
                flat.append(h if isinstance(h, str) else next((x for x in h if x), ""))
            n = len(flat)
            scores[name] = weight * (1 + 0.25 * min(n - 1, 4))
            evidence[name] = sorted({s.lower().strip() for s in flat if s})[:5]
    if not scores:
        return "other", 0.0, []
    best = max(scores, key=scores.get)
    total = sum(scores.values())
    return best, round(scores[best] / total, 3), evidence.get(best, [])
