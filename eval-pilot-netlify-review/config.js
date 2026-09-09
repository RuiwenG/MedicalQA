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

  // Current study table. The five-metric rubric (Standalone added) and the
  // regenerated SingleAgent Q&As get their own table, so `ratings_ui_v2_qna_v2`
  // stays a clean four-metric record and nothing has to be filtered by NULLs.
  RATINGS_TABLE: "ratings_v3",
  STUDY_VERSION: "v3",

  // The study is SingleAgent-only (new self-contained prompt). This is an
  // allowlist, so nothing else can enter the corpus — not MultiAgent v2/v3, not
  // DualAgent or RAG, not SingleAgent-prev (the pre-prompt-change outputs kept
  // on disk purely as a judge comparison arm), and not any approach folder added
  // later. Excluded pairs never reach the shared ordered set, the advanced
  // picker, or any count; qa_data.json still holds them all.
  ONLY_APPROACHES: ["SingleAgent"],

  // Ignored while ONLY_APPROACHES is non-empty. Kept for the older setup.
  EXCLUDE_APPROACHES: ["DualAgent", "RAG"],

  // Hard cap on a session (0 = no cap). The same first N ordered pairs are shown
  // to every evaluator.
  // The Netlify pilot caps a session; eval/web runs uncapped.
  MAX_PAIRS: 40,
};
