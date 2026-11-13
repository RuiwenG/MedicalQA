# Deploy the existing v5 study on Cloudflare Pages

Use your existing Cloudflare account. This is a static copy of the v5 evaluation app with the existing public Supabase configuration. It uses the same live database, quotas, and five Q&A groups. No SQL migration is needed.

## Upload

1. Sign in at https://dash.cloudflare.com/ and select your account.
2. Open **Workers & Pages**, then **Create application**. Choose **Pages** and **Use direct upload** / **Drag and drop your files** (labels can vary).
3. Choose a project name, such as `medicalqa-eval-v5` if available.
4. Upload `eval-v5-cloudflare.zip`, or drag the `eval-v5-cloudflare` folder. The ZIP has `index.html` at its root. No build command or GitHub connection is needed.
5. Select **Deploy site**. Open the project's permanent `*.pages.dev` address. Use a normal Pages project, not a temporary Cloudflare Drop preview.

## Use your domain

Use a free subdomain such as `eval.yourdomain.com` to preserve your existing main website. In the Pages project, open **Custom domains → Set up a domain**, then enter your chosen subdomain. Associate it with Pages before manually creating DNS records.

If your DNS is managed in the same Cloudflare account, follow its prompt to add the record. If your DNS is elsewhere, add the CNAME Cloudflare specifies at that provider, pointing the subdomain to your actual Pages project hostname. You do not need to move your whole domain to Cloudflare just to use a subdomain.

## Check before sharing

- Confirm the site opens over HTTPS, without a Local-only mode notice.
- Confirm source clips and their YouTube links open.
- Let a clip finish, then play again. It should restart at the suggested start,
  with the same clip end, using the player's own controls.
- After Standalone, open **Read the clip transcript**. Check that it shows
  only that clip's captions as plain text, with the range once in the heading
  and no per-line timestamps or full-video option. Upload the complete refreshed
  ZIP; the transcript update needs no SQL changes.
- Ask someone physically in Egypt to check it on their normal connection, without a VPN. A different host cannot guarantee availability on every network.
- If you submit a test evaluation, use a clearly named test participant and remove only that test assignment and its ratings afterward. Signups and submissions use the real shared database.
- Choose the final domain before distributing the new link. Browser progress is specific to an origin: Netlify, `pages.dev`, and your custom domain do not share saved browser sessions. Existing participants should continue their original URL to avoid creating another assignment. Responses already submitted remain in Supabase.

The upload contains only `index.html`, `config.js`, `qa_data.json`, `transcripts.json`, `versions.json`, and `_headers`. These are byte-identical to the local Netlify runtime files when packaged. `_headers` preserves the YouTube-compatible referrer policy. The browser config contains the existing public anon key, not a service-role key or database password.

Official instructions:
- https://developers.cloudflare.com/pages/get-started/direct-upload/
- https://developers.cloudflare.com/pages/configuration/custom-domains/
