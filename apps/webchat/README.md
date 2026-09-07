# Customers Manager HUB Webchat

`apps/webchat` is the lightweight customer-facing Website Chat artifact. It is intentionally
separate from the Next.js Admin Console (`apps/web`) and has no framework/runtime dependency.

## Embed

Host `widget.js` from your static asset/CDN origin, then embed it on an origin configured for the
Website channel account:

```html
<script
  src="https://static.example.com/customers-manager-hub/widget.js"
  data-api-base="https://hub.example.com"
  data-channel-account-id="00000000-0000-0000-0000-000000000000"
  defer
></script>
```

The widget creates a Shadow DOM floating chat UI, obtains a scoped public Website session,
persists the session ID/token in browser local storage when available, posts retry-safe UUID
message IDs, and polls the canonical public-message endpoint while the panel is open.

Do not place Admin credentials, tenant secrets, AI provider keys, or business API credentials in
widget attributes or source.
