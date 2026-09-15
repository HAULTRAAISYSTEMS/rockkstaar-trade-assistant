# Free browser push alerts

Tradestaar uses standards-based Web Push. There is no SMS provider, phone
number, Apple Developer membership, Firebase project, or per-message bill.
The existing stable `SECRET_KEY` deterministically supplies the VAPID identity
and encrypts stored push-service capabilities. Production may instead set
`WEB_PUSH_ENCRYPTION_KEY` and `WEB_PUSH_VAPID_SUBJECT` explicitly.

## User setup

### Desktop and Android

1. Open **Account → Free Browser Alerts** in a current browser.
2. Select **Enable free alerts** and allow the browser permission prompt.
3. Select **Send test alert**.

### iPhone and iPad

1. Open Tradestaar in Safari.
2. Use **Share → Add to Home Screen**.
3. Launch Tradestaar from the new Home Screen icon.
4. Open **Account → Free Browser Alerts** and select **Enable free alerts**.
5. Allow the iOS notification prompt and send a test alert.

iOS permits Web Push only for installed Home Screen web apps and permission
must follow a direct user action. Users manage it later in iOS Notifications
settings like a native app.

## Delivery boundary

The five-minute research runner considers only published items from the last
24 hours. A notification requires a saved thesis and the deterministic
materiality threshold. Quiet hours, timezone, a daily cap, and per-device
idempotency apply before delivery. Expired browser subscriptions are disabled
after the push service returns HTTP 404 or 410.

Subscription endpoints are capability URLs and are never stored as plaintext.
Tradestaar encrypts the complete subscription and stores only a SHA-256 lookup
hash of its endpoint.
