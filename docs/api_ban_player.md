# Ban Player API Reference

## `POST /api/ban_player/`

Apply or remove a moderation action (ban, sus, shun) on a Tower player.

---

## Authentication

Include your API key in every request header:

```
X-API-KEY: <your-api-key>
```

API keys are issued by the site administrators. Missing or invalid keys receive `403 Forbidden`.

---

## Request Body (JSON)

| Field       | Type             | Required | Notes                                                        |
| ----------- | ---------------- | -------- | ------------------------------------------------------------ |
| `player_id` | string (max 32)  | **Yes**  | Tower player ID (hex string, e.g. `C14A14D35BB8AA5A`)        |
| `action`    | string           | **Yes**  | One of: `ban`, `unban`, `sus`, `unsus`                       |
| `note`      | string (max 500) | No       | Reason or ticket reference — stored on the moderation record |

---

## Responses

| Status                      | Meaning                                                                                                             |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------- |
| `200 OK`                    | Action applied successfully. Body: `{"detail": "Player <id> marked as <action>."}`                                  |
| `400 Bad Request`           | Missing/invalid fields, or `unban`/`unsus` when no active record exists. Body: `{"detail": "...", "errors": {...}}` |
| `403 Forbidden`             | API key missing, inactive, or lacks permission                                                                      |
| `500 Internal Server Error` | Unexpected server-side error                                                                                        |

---

## Action Semantics

| Action  | Effect                                                                                                                                                                                      |
| ------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `ban`   | Creates a new active BAN record. Auto-resolves any active SUS record for the same player. Sends a real-time socket notification to the Discord bot to remove the verified role immediately. |
| `sus`   | Creates a new active SUS record.                                                                                                                                                            |
| `unban` | Resolves (closes) all active BAN records for the player. Returns `400` if no active BAN record exists.                                                                                      |
| `unsus` | Resolves (closes) all active SUS records for the player. Returns `400` if no active SUS record exists.                                                                                      |

**Idempotency note:** If an active record of the same type already exists and was created via API,
the action is a no-op that returns `200`. If the existing record was created manually, it gets
re-attributed to the API key (reinforced).

---

## Examples

### Ban a player

```http
POST /api/ban_player/ HTTP/1.1
Content-Type: application/json
X-API-KEY: abc123...

{
  "player_id": "C14A14D35BB8AA5A",
  "action": "ban",
  "note": "Banned via ticket #65371"
}
```

Response `200 OK`:

```json
{ "detail": "Player C14A14D35BB8AA5A marked as ban." }
```

---

### Mark a player as suspicious

```http
POST /api/ban_player/ HTTP/1.1
Content-Type: application/json
X-API-KEY: abc123...

{
  "player_id": "C14A14D35BB8AA5A",
  "action": "sus",
  "note": "Suspicious score pattern in tournament #42"
}
```

---

### Lift a ban

```http
POST /api/ban_player/ HTTP/1.1
Content-Type: application/json
X-API-KEY: abc123...

{
  "player_id": "C14A14D35BB8AA5A",
  "action": "unban",
  "note": "Appeal approved"
}
```

Response `400 Bad Request` (if no active ban exists):

```json
{ "detail": "No active ban record found for player C14A14D35BB8AA5A." }
```
