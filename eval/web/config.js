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

  // Current study table. This separates the updated Q&A set + updated UI rubric
  // from earlier pilot annotations in the legacy `ratings` table.
  RATINGS_TABLE: "ratings_ui_v2_qna_v2",
  STUDY_VERSION: "ui_v2_qna_v2",

  // Approaches dropped from the corpus before anything else runs. Excluded
  // pairs never enter the shared ordered set, the advanced picker, or any count.
  // qa_data.json still holds them — empty this list to bring them back.
  EXCLUDE_APPROACHES: ["DualAgent", "RAG"],

  // Hard cap on a session (0 = no cap). The same first N ordered pairs are shown
  // to every evaluator.
  MAX_PAIRS: 0,
};
