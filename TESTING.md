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

When MongoDB is absent, normal development and production fail closed.
In-memory storage is available only when `DEMO_MODE=true` is explicitly set.

## Local Brevo password reset test

1. Verify a sender address in Brevo.
2. Set `MAIL_PROVIDER=brevo_api` and `MAIL_ENABLED=true`.
3. Set `BREVO_API_KEY`, `BREVO_SENDER_EMAIL`, and `BREVO_SENDER_NAME` locally without committing them.
4. Set `APP_BASE_URL=http://127.0.0.1:5000`.
5. Start the Flask application.
6. Register or use a test account whose mailbox you control.
7. Open **Forgot password** from the sign-in page and submit the email.
8. Open the received link and set a new password.
9. Confirm the old password fails, the new password succeeds, and the reset link cannot be reused.
10. Remove the local Brevo API key after the controlled test if it is no longer needed.

Automated tests mock `requests.post` and must never send a live message. Logs and test output must not contain the API key, password, raw reset token, or complete reset URL.
