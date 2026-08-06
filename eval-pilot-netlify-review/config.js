// Review-only Netlify settings for the dementia QA evaluation site.
// Supabase is disabled so reviewers do not write test rows to the study database.

window.EVAL_CONFIG = {
  SUPABASE_URL: "",
  SUPABASE_ANON_KEY: "",
  SEED: 42,
  BATCHES: 8,
  ANCHOR_SIZE: 20,
  MAX_PAIRS: 40,
  // Must match eval/web/config.js — the pilot rates the same corpus.
  EXCLUDE_APPROACHES: ["DualAgent", "RAG"],
};
