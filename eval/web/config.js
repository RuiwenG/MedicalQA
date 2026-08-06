// Deployment settings for the dementia QA evaluation site.
//
// Both Supabase values below are PUBLIC by design — the anon key is safe to
// ship in a static page because the `ratings` table grants `anon` INSERT only
// (see supabase_schema.sql). It cannot read, edit, or delete anything.
//
// Leave them empty to run in local-only mode: everything still works, ratings
// are kept in the browser, and annotators export a CSV at the end. The page
// shows a banner so you notice the backend is not wired up.

window.EVAL_CONFIG = {
  // Project settings -> Data API -> Project URL  (https://xxxx.supabase.co)
  // Just the project origin — no /rest/v1 suffix, the app adds that itself.
  SUPABASE_URL: "https://mnmjmxcjhyiawrdxojif.supabase.co",

  // Project settings -> API keys -> anon / public
  SUPABASE_ANON_KEY: "sb_publishable_5XnfPkcVK9OjGhroW5Q7Ww_bLrgx0u7",

  // Drives batch assignment and shuffling. Every annotator must share the same
  // seed or the batches stop lining up. Change it only to redraw the study.
  SEED: 42,

  // Approaches dropped from the corpus before anything else runs. Excluded
  // pairs never enter a batch, the anchor set, the advanced picker, or any
  // count. qa_data.json still holds them — empty this list to bring them back.
  EXCLUDE_APPROACHES: ["DualAgent", "RAG"],

  // How many batches the corpus is split into. Roughly one per annotator:
  // 987 pairs after exclusions / 8 batches ~= 121 pairs each, plus the anchor
  // set below.
  BATCHES: 8,

  // Pairs given to EVERY annotator on top of their own batch. This overlap is
  // what makes inter-annotator agreement computable. 0 disables it.
  ANCHOR_SIZE: 20,

  // Hard cap on a session (0 = no cap). Useful for a short pilot: set to 40 and
  // everyone rates 40 pairs instead of ~218.
  MAX_PAIRS: 0,
};
