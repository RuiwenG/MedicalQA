// Pilot Netlify settings for the dementia QA evaluation site.
// Wired to the study Supabase so pilot ratings land in the database directly —
// no CSV download/collection needed. The anon key is PUBLIC by design: the
// ratings table grants `anon` INSERT and nothing else (see
// supabase_schema.sql), so it cannot read, edit, or delete anything.

window.EVAL_CONFIG = {
  SUPABASE_URL: "https://mnmjmxcjhyiawrdxojif.supabase.co",
  SUPABASE_ANON_KEY: "sb_publishable_5XnfPkcVK9OjGhroW5Q7Ww_bLrgx0u7",
  RATINGS_TABLE: "ratings_ui_v2_qna_v2",
  STUDY_VERSION: "ui_v2_qna_v2",
  MAX_PAIRS: 40,
  // Must match eval/web/config.js — the pilot rates the same corpus.
  EXCLUDE_APPROACHES: ["DualAgent", "RAG"],
};
