# Material thesis alerts by text

SMS is a separate opt-in delivery boundary. Research ingestion cannot send a
message. The five-minute Live Research runner may deliver only a published item
that clears the deterministic materiality threshold and matches a saved thesis.

## Provider configuration

Tradestaar uses Twilio's Messages resource. Configure these secrets in both the
Render web service and the `live-research-runner` cron job:

- `SMS_ENCRYPTION_KEY`: a long, stable random secret. Changing it makes existing
  encrypted phone numbers unreadable.
- `TWILIO_ACCOUNT_SID`
- `TWILIO_API_KEY` and `TWILIO_API_SECRET` (preferred for production), or
  `TWILIO_AUTH_TOKEN` for initial testing
- One sender: `TWILIO_MESSAGING_SERVICE_SID` (preferred) or
  `TWILIO_FROM_NUMBER` in E.164 form.

Set `SMS_ALERTS_ENABLED=1` on the cron job only after both services have the
same secrets. Credentials must remain environment variables and must never be
committed.

Twilio trial accounts can text only verified recipient numbers. US application
traffic may also require the sender's applicable registration and compliance
configuration in Twilio before carriers will deliver it reliably.

## User opt-in

1. Open **Account → Text Alerts**.
2. Enter the destination number and explicitly consent.
3. Enter the six-digit code received by text.
4. Choose quiet hours, timezone, and a daily cap.
5. Send one test text.

The database stores an encrypted phone number and a four-digit display suffix.
Verification codes are HMAC hashes, expire in ten minutes, and allow five
attempts. A delivery reservation is committed before contacting Twilio, so an
ambiguous network failure cannot produce a duplicate message after a restart.
Twilio handles standard STOP keywords; provider opt-out errors also pause the
local preference.
