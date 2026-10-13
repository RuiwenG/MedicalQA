// Deployment settings for the dementia QA evaluation site.
//
// Both Supabase values below are PUBLIC by design — the anon key is safe to
// ship in a static page because the ratings table grants `anon` INSERT only
// (see ../eval-v4-sql/01_create_ratings_v4.sql). It cannot read, edit, or
// delete anything.
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

  // v4 is isolated from the frozen v3 study. Do not point this package back to
  // ratings_v3: the corpus, rubric and browser-session namespace are versioned
  // together.
  RATINGS_TABLE: "ratings_v4",
  STUDY_VERSION: "v4",

  // The study is SingleAgent-only (new self-contained prompt). This is an
  // allowlist, so nothing else can enter the corpus — not MultiAgent v2/v3, not
  // DualAgent or RAG, not SingleAgent-prev (the pre-prompt-change outputs kept
  // on disk purely as a judge comparison arm), and not any approach folder added
  // later. Excluded pairs never reach the shared ordered set, the advanced
  // picker, or any count; qa_data.json still holds them all.
  ONLY_APPROACHES: ["SingleAgent"],

  // Ignored while ONLY_APPROACHES is non-empty. Kept for the older setup.
  EXCLUDE_APPROACHES: ["DualAgent", "RAG"],

  // Hard cap on a session (0 = no cap). The v4 Netlify pilot keeps the prior
  // 40-pair burden; change this deliberately before launching a full-corpus run.
  // Every evaluator receives the same first N pairs in deterministic order.
  MAX_PAIRS: 40,
};
