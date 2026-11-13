// Deployment settings for the dementia QA evaluation site, v5.
//
// Both Supabase values below are PUBLIC by design — the anon key is safe to
// ship in a static page because the browser role may only call
// claim_version_v5 and append to ratings_v5 (see
// ../eval-v5-sql/01_create_v5.sql). It cannot read, edit, or delete anything.
//
// Configured for the medicalqa-eval Supabase project that holds this pilot's
// v5 objects. The key below is the public anon key, not a service-role key.
//
// Left empty, the page runs in local-only mode for testing: no quotas, version
// 1 (or ?version=N), ratings kept in the browser and exported at the end.

window.EVAL_CONFIG = {
  // Project settings -> Data API -> Project URL  (https://xxxx.supabase.co)
  // Just the project origin — no /rest/v1 suffix, the app adds that itself.
  SUPABASE_URL: "https://mnmjmxcjhyiawrdxojif.supabase.co",

  // Project settings -> API keys -> anon / public
  SUPABASE_ANON_KEY: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1ubWpteGNqaHlpYXdyZHhvamlmIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODU4MjA2MjAsImV4cCI6MjEwMTM5NjYyMH0.kALj7MImQktgmj4-REikm4GGe-49GGQcK_CRFWgzIWU",

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
    "SingleAgent-qwen3.5-9b-v3",
    "SingleAgent-qwen3.5-9b-bad-v3",
  ],
};
