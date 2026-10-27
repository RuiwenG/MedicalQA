// Deployment settings for the dementia QA evaluation site, v5.
//
// Both Supabase values below are PUBLIC by design — the anon key is safe to
// ship in a static page because the browser role may only call
// claim_version_v5 and append to ratings_v5 (see
// ../eval-v5-sql/01_create_v5.sql). It cannot read, edit, or delete anything.
//
// They are left empty in the repository. Fill them in for the Supabase project
// that holds the v5 objects — the existing v4 project or a brand-new one; v5
// shares nothing with v1-v4, so either works.
//
// Left empty, the page runs in local-only mode for testing: no quotas, version
// 1 (or ?version=N), ratings kept in the browser and exported at the end.

window.EVAL_CONFIG = {
  // Project settings -> Data API -> Project URL  (https://xxxx.supabase.co)
  // Just the project origin — no /rest/v1 suffix, the app adds that itself.
  SUPABASE_URL: "",

  // Project settings -> API keys -> anon / public
  SUPABASE_ANON_KEY: "",

  // v5 is isolated from every earlier study: its own table, routing function
  // and browser-storage namespace (dqa:v5:*). Do not point it at ratings_v4.
  RATINGS_TABLE: "ratings_v5",
  STUDY_VERSION: "v5",
  CLAIM_FUNCTION: "claim_version_v5",

  // Only the paper's main generator, plus its deliberately flawed pairs.
  // qa_data.json already holds just the selected pairs; this allowlist is a
  // second guard. The UI never shows the approach name.
  ONLY_APPROACHES: [
    "SingleAgent-qwen3-14b-v1",
    "SingleAgent-qwen3-14b-v2",
    "SingleAgent-qwen3-14b-v3",
    "SingleAgent-qwen3-14b-bad-v3",
  ],
};
