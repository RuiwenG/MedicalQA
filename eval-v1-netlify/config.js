// Deployment settings for the dementia QA evaluation site.
//
// Both Supabase values below are PUBLIC by design — the anon key is safe to
// ship in a static page because the ratings table grants `anon` INSERT only
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

  // Separate table for the older v1 prompt comparison arm. This keeps the v1
  // human ratings completely separate from the active v3 evaluation table.
  RATINGS_TABLE: "ratings_v1",
  STUDY_VERSION: "v1",

  // First-deployed SingleAgent outputs from Git commit d580199 (2026-08-02).
  // The historical corpus is relabelled SingleAgent-v1 so stored ratings cannot
  // be confused with either SingleAgent-prev or the active SingleAgent corpus.
  ONLY_APPROACHES: ["SingleAgent-v1"],

  // Ignored while ONLY_APPROACHES is non-empty. Kept for the older setup.
  EXCLUDE_APPROACHES: ["DualAgent", "RAG"],

  // Hard cap on a session (0 = no cap). The same first N ordered pairs are shown
  // to every evaluator.
  // Match the active pilot's evaluator workload.
  MAX_PAIRS: 40,
};
