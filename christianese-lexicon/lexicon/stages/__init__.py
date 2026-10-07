from . import (s01_ingest, s02_counts, s03_seeker, s04_keyness, s05_shortlist, s06_contexts, s07_polysemy,
               s08_stance, s09_grounding, s10_rank, s11_draft, s12_dimensions, s13_coverage, s14_report)

STAGES = [s01_ingest, s02_counts, s03_seeker, s04_keyness, s05_shortlist, s06_contexts, s07_polysemy,
          s08_stance, s09_grounding, s10_rank, s11_draft, s12_dimensions, s13_coverage, s14_report]

# stages that call the model (useful for estimating run time)
USES_LLM = {"03_seeker", "07_polysemy", "08_stance", "11_draft", "12_dimensions", "13_coverage"}
