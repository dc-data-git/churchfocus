# SPEC: methods, formulas, outputs

This is the reference for *why* each number means what it means, so the build doc can
cite it and a reviewer can challenge it.

## Goal

Produce a ranked, evidence-backed list of church vocabulary terms, each with:

1. **priority**: how often seekers use it when describing what they want or want to avoid;
2. **ambiguity profile**: polysemous (meaning varies by tradition), contested (approving vs
   critical usage), negatable ("not legalistic"), tradition-marking;
3. **grounding**: the observable words that co-occur with it;
4. **a drafted entry** that maps every sense to features in `data/features.yaml`, plus a
   neutral disambiguation question and (for contested terms) neutral options.

## Two corpora

- **seeker-side**: people describing the church they want. Drives priority.
- **church-side**: churches describing themselves, labeled by tradition. Drives tradition
  markers and meaning-drift.

The gap between them is the point: words seekers use that churches don't (and vice
versa) are where translation is needed.

## Stage details

### 01 ingest
Drop all fields except `id, text, side, tradition, source, date`. Scrub URLs, emails,
@handles, phone numbers. Drop docs < 5 tokens. Dedupe on normalized token string.
Hold out `split.seeker_holdout_fraction` of seeker docs by a stable hash. Held-out docs
are never used in stages 02–12.

### 02 counts
Lowercase regex tokens (hyphenated words kept whole: `spirit-filled`). Count 1..`max_ngram`
grams overall, by side, by tradition (church side).
Cohesion for n ≥ 2: **NPMI** = log(p(g) / Π p(wᵢ)) / −log p(g), where p(g) is relative to
all n-grams of that order. Candidates: content n-grams (no leading/trailing stopword)
with count ≥ `min_count` and NPMI ≥ `min_npmi`, plus every seed term.

### 03 seeker mining
Request sentences are matched by patterns ("looking for a church", "church that/with",
"deal breaker", "new in town", …) plus the following sentence. A 7B model extracts
attributes `{phrase, polarity: want|avoid, category}`. **Self-check:** every phrase must
occur verbatim in the input. Otherwise the error is sent back for repair (up to
`json_repair_attempts`), then the call is logged as failed. Phrases are normalized
(stopwords trimmed) and aggregated.

### 04 keyness
Log-odds ratio with informative Dirichlet prior (Monroe, Colaresi & Quinn 2008):

    δ = log((yᵢ+α_w)/(nᵢ+α₀−yᵢ−α_w)) − log((yⱼ+α_w)/(nⱼ+α₀−yⱼ−α_w))
    σ² ≈ 1/(yᵢ+α_w) + 1/(yⱼ+α_w)        z = δ/σ

- **vs English:** j = the `wordfreq` English frequency × `reference_corpus_size` (a pseudo-corpus).
  wordfreq over-estimates multi-word phrase frequency, so phrase keyness is conservative.
- **tradition markers:** i = one tradition's church text, j = all other church text;
  α_w = α₀ · (term's church-side rate). Traditions with < `min_tradition_docs` are skipped.

### 05 shortlist
Cheap score = z(log1p(seeker requests + seeker-side count)) + z(z_english) + 1·seed.
Non-seed terms are excluded if they are in `generic_terms`, more common in English than
`max_ref_freq`, or **subsumed** (≥ `subsume_ratio` of their occurrences lie inside one
longer candidate, e.g. "call" inside "altar call"). Seed terms and seeker phrases asked
≥ 2 times are always kept. Top `size` overall.

### 06 contexts (single pass)
Per shortlisted term: count by side and tradition; **negation** = a negator (not, no,
never, n't, without, anti, non, avoid, …) within 3 tokens before; observable co-occurrence
within ±`grounding.window`; reservoir-sampled contexts (±`context_window_tokens`) per
group (seeker, church:<tradition>), merged round-robin so traditions are balanced.

### 07 polysemy
Embed contexts. k-means for k in `k_range`, best cosine silhouette → **polysemy** =
max(0, silhouette). **drift** = max over tradition pairs of (1 − cos) between mean
context embeddings (groups with ≥ 5 contexts). **nmi** = NMI(sense cluster, tradition).
High drift is the strongest single signal that a word needs a "which tradition?" question.

### 08 stance
One 7B call per term labels up to `sample_per_term` numbered contexts approving /
critical / neutral / mixed. **Self-check:** exactly one label per index.
**contestedness** = 2·min(p_approving, p_critical), counting mixed as ½ each (1.0 = evenly
split). **tradition_split** = max − min approval rate across groups with ≥ 3 labels.

### 09 grounding
lift(t, o) = P(o in window | t) / P(o in a random window of 2·w tokens). Report log₂ lift
for observables with ≥ `min_cooccurrence` co-occurrences, top `top_k`.

### 10 rank
ambiguity_raw = 0.35·polysemy + 0.25·drift + 0.25·contestedness + 0.15·negation_rate
score = w_seeker·z(log1p demand) + w_keyness·z(z_english) + w_ambiguity·z(ambiguity_raw) + w_seed·seed

### 11 draft
14B model, schema-constrained (`schemas/lexicon_entry.schema.json`). The prompt carries the
full feature vocabulary, the term's statistics, its top observables and balanced contexts.
**Semantic self-checks** (sent back for repair): feature ids must be in the vocabulary or
`proposed.*`; `polysemous` ⇒ ≥ 2 senses; `contested` ⇒ 2–4 neutral options;
polysemous/contested/evaluative ⇒ non-empty disambiguation question. Terms the model marks
`not_relevant` go to `rejected.jsonl`. Every entry gets `provenance.vetted = false`.

### 12 dimensions
Embed "term: glosses", k-means (`n_clusters`), model names each cluster; cross-tab against
drafted `category`. A low-purity or unmatched cluster suggests a dimension we're missing.

### 13 coverage (evaluation)
Run the stage-03 extractor on held-out seeker docs. A requested phrase is covered if it
token-contains, or is token-contained in, a lexicon term or alias.
**coverage = covered / all requested attributes**, also reported per category, plus the
top uncovered phrases. Run again after review on the vetted lexicon.

## Output: lexicon.json (consumed by the MCP server)

```json
{
  "schema_version": "lexicon.v1",
  "generated_at": "...",
  "dry_run": false,
  "coverage": {"holdout_attributes": 812, "coverage": 0.63, "by_category": {"worship": 0.71}},
  "entries": [
    {
      "term": "traditional",
      "relevance": "church_vocabulary",
      "aliases": ["traditional worship"],
      "category": "worship",
      "term_types": ["polysemous"],
      "senses": [
        {"gloss": "...", "traditions": ["baptist"], "features": [{"feature": "worship.music.choir", "value": "present"}]},
        {"gloss": "...", "traditions": ["anglican", "lutheran"], "features": [{"feature": "worship.liturgy", "value": "high"}]}
      ],
      "disambiguation_question": "...",
      "neutral_options": [],
      "notes": "...",
      "evidence_stats": {"rank": 3, "score": 2.1, "count": 412, "seeker_want": 37, "drift": 0.41, "observables": []},
      "provenance": {"drafted_by": "qwen2.5:14b", "prompt_version": "draft.v1", "vetted": false, "reviewers": []}
    }
  ]
}
```

## Known limitations (put these in the build doc)

- Keyness against wordfreq is approximate for phrases and for platform-specific English.
- Polysemy/drift depend on enough labeled church-side text per tradition. With thin data
  they are reported as null, not zero.
- The seeker extractor depends on request-sentence patterns. Requests phrased unusually
  are missed (`top_uncovered` and recall checks will show this).
- Drafted entries are model output until reviewed. Inter-rater kappa is the honest
  measure of how reliable the review itself is.
