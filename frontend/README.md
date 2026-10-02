# Seat Map Frontend

This dependency-free page displays a show's live seat state using the existing `GET /shows/{id}` API. It refreshes every three seconds. Available seats can be selected and reserved; held and confirmed seats are disabled. Reservations created here can be cancelled from the same browser session.

Start the API first, then run:

```sh
API_BASE_URL=http://localhost:18000 python3 frontend/server.py --port 4173
```

Open `http://127.0.0.1:4173/?show=SHOW_UUID`. Set `API_BASE_URL` to the reachable API origin; it defaults to `http://127.0.0.1:8000`. The local server serves the page and proxies the existing show, reserve, and cancel routes, so the API requires no CORS changes.

Enter the mock user ID used as the API bearer token to reserve seats. The ID is not stored by the page. A pending reservation request stores its idempotency key and payload in session storage so a retry uses the same key. Reservation IDs are also kept in session storage for cancellation; they are not discoverable after clearing the browser session because the API has no reservation-list endpoint.