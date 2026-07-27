# Precision Curator verification

## Automated checks

```powershell
python -m pytest tests/test_precision_app.py -q
```

The focused tests verify the public routes, mock-login role routing, access
control, user-specific search records, generated demo search persistence and
record-ID analytics.

## Manual smoke test

1. Copy `.env.example` to `.env`, configure MongoDB, and run `python app.py`.
2. Open `/`, `/login`, and `/demo` without a session.
3. Log in once per role and confirm the three dashboards contain different
   data and that cross-role dashboard URLs return 403.
4. As a consumer, search with **Generated Demo**, open its analytics link, and
   confirm the record, products and analytics report exist in MongoDB.
5. Search with **eBay API** and confirm source links, `ebay_api`, collection
   timestamp and high confidence are shown.
6. Search with **AI Web Search** and confirm every result has a price and public
   source link. Temporarily remove `OPENAI_API_KEY` and confirm a readable error.
7. Confirm Walmart is marked experimental and AI content is described as
   decision support throughout the UI.

When MongoDB is absent, the application starts in a visibly labeled in-memory
development fallback. This mode is deliberately non-persistent and is not the
deployment data strategy.

## Local password reset test

1. Set `EMAIL_MODE=console`.
2. Set `APP_BASE_URL=http://127.0.0.1:5000`.
3. Start the Flask application.
4. Register or use a test account with an email and password.
5. Open **Forgot password** from the sign-in page.
6. Submit the test email.
7. Copy the reset URL printed in the terminal.
8. Open the URL in the same local browser and set a new password.
9. Confirm the old password fails and the new password succeeds.
10. Confirm the reset URL cannot be reused.

Console mode never opens an SMTP connection. To enable SMTP later, set
`EMAIL_MODE=smtp` and configure the `MAIL_*` values documented in
`.env.example`.
