// Public browser configuration for the MedicalQA v6 study.
window.EVAL_CONFIG = {
  SUPABASE_URL: "https://mnmjmxcjhyiawrdxojif.supabase.co",
  SUPABASE_ANON_KEY: "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Im1ubWpteGNqaHlpYXdyZHhvamlmIiwicm9sZSI6ImFub24iLCJpYXQiOjE3ODU4MjA2MjAsImV4cCI6MjEwMTM5NjYyMH0.kALj7MImQktgmj4-REikm4GGe-49GGQcK_CRFWgzIWU",
  STUDY_VERSION: "v6",
  RATINGS_TABLE: "ratings_v6",
  CLAIM_FUNCTION: "claim_version_v6",
  ONLY_APPROACHES: [
    "SingleAgent-qwen3-14b-v1",
    "SingleAgent-qwen3-14b-v2",
    "SingleAgent-qwen3-14b-v3",
    "SingleAgent-qwen3-14b-bad-v3",
    "SingleAgent-qwen3.5-9b-v3",
    "SingleAgent-qwen3.5-9b-bad-v3",
  ],
};
