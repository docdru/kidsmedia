# Kids Media — Specification

Status: draft v0.21 · 2026-11-14 (v0.21: full-screen button for video, §8 · v0.20: public code, family image with JOY, the story packs and the signed APK, §18 · v0.19: Done activities in the admin: bonus star per task, open again, §17 · v0.18: one language button, Home header on narrow screens, gems screen · v0.17: gems 💎, rules that unlock by gems instead of time, §16 · v0.16: no accidental done, photos of drawings and stories, maths problem sets, simple materials, §15 · v0.15: story language set per queued story, language flags for the child, §14.7 · v0.14: levels, reading, read aloud on the server, rewards, §14 · v0.13: pictures in story packs · v0.12: Greek as a third language, Greek mystery world · v0.11: activities, §13 · v0.10: volume cap per tablet · v0.9: covers without a MIME type · v0.8: release images carry their APK, silent self-update · v0.7: a session belongs to the day it started · v0.6: grace at the midnight rollover, grace per rule, final heartbeat · v0.5: #2/#3 decided · v0.2: open questions resolved, §12 · v0.3: thumbnails, §6.4 · v0.4: review fixes: grace on stream, evening-anchored lockouts, resume per group, cumulative heartbeats, cover fallback)

A self-hosted media server for a child's Android tablet. A parent uploads music, audiobooks and videos to a Docker web app on a public Linux server, puts them into groups, and sets usage rules. A dedicated Android app on the tablet streams the media and enforces the rules.

---

## 1. Goals and non-goals

### Goals
- **One simple app for the child.** Big picture tiles, no menus, no ads, no store. A child who can't read yet can use it.
- **Parent control lives on the server.** All content, rules and usage history are managed in a web admin. Nothing is configured on the tablet after pairing.
- **Rules per group.** For example: one audiobook file per day from two series combined, only on weekends, while music is allowed much longer. Plus lock-out windows such as bedtime.
- **Smooth streaming.** Generous buffering so playback survives short Wi-Fi drops. Audio keeps playing with the screen off.
- **Easy to run.** One `docker compose up` in `/opt/kidsmedia`, a single SQLite file plus a media folder, behind the server's existing nginx (HTTPS via Let's Encrypt).
- **Three languages.** Everything ships in English, German and Greek (`en`, `de`, `el`): the tablet app, the admin, the child-facing messages and the story packs. English and German were there from day one; Greek was added in v0.12.

### Non-goals (v1)
- Several children in the UI. v1 has one child, but the data model is **profile-aware from the start** (§6.1), so adding children later is a UI feature, not a migration of every table.
- Offline downloads and offline playback. These are planned for a later milestone (M7, §11), and v1 API choices keep that path open (stable ids, ETags, server-side usage log that can accept offline batches).
- Server-side transcoding. Uploads must already be playable (see §6.3).
- Google Play distribution. The APK is sideloaded.
- Watertight protection against a technically determined child. Rules are enforced on the server (start and stream) and in the app (countdown). A rooted device or a stolen token is out of scope.

---

## 2. Glossary

| Term | Meaning |
|---|---|
| **Group** | A named collection of media files shown to the child as one tile, e.g. "Music", "The Moon Pirates" (an audiobook series), "Cartoons". It has a kind (`music`, `audiobook`, `video`), a cover, a colour and an order. |
| **Media item** | One uploaded file (mp3 / m4a / mp4) in exactly one group, with its title, duration, thumbnail and position in the group. |
| **Thumbnail** | The picture for one media item. It is taken automatically from the file (embedded cover art, or a video frame) and can be replaced by the parent (§6.4). |
| **Group cover** | The picture on a group's home tile. By default it is the first item's thumbnail, and it can be replaced by an upload. |
| **Rule** | A usage constraint attached to one or more groups (or all groups), active on chosen weekdays. |
| **Play** | The first start of a media item on a calendar day. A play uses up an item once it has played for the rule's `count_after_seconds` that day (§3.3). Counting plays is how item limits work. |
| **Device** | A paired tablet with a revocable bearer token. |
| **Day** | A calendar day in the server's configured time zone (`TZ`), midnight to midnight. |
| **Session** | One run of one media item, from `POST /play/start` until the app starts another item or stops. A session **belongs to the day it started**, also when it runs past midnight (§3.3). |

---

## 3. Rules model (core of the product)

### 3.1 Rule fields

| Field | Type | Meaning |
|---|---|---|
| `name` | string | Shown in the admin and in the child's "why is it locked" hint. |
| `enabled` | bool | Disabled rules are ignored. |
| `days` | set of weekday 0–6 (Mon=0) | Days on which the rule is active. |
| `scope` | `all` or a list of group ids | Groups the rule applies to. |
| `mode` | `limit` or `block` | `block` makes the groups unavailable on those days. `limit` applies the limits below. |
| `max_items_per_day` | int? | Maximum number of **different** files started per day, counted **together** across all groups in the scope. |
| `max_minutes_per_day` | int? | Maximum playback minutes per day, counted together across the scope. |
| `lockouts` | list of `HH:MM–HH:MM` | Time windows when the groups are locked. A window may cross midnight (`19:30–07:00`). A window belongs to the day it **starts** on (§3.3b). |
| `grace_minutes` | int, default 0 | "Finish what you're on" allowance (§3.3a). |
| `replay_percent` | int 100–1000 or null, default 120 | Listening budget of a counted file per day, in % of its length (§3.3). When it is used up, the file is done for today. `null` = unlimited replays. Only applies together with `max_items_per_day` or `gem_cost` (§16). |
| `count_after_seconds` | int 0–600, default 10 | A started file uses up one of `max_items_per_day` only after it has played this long today (§3.3). Accidental taps and quick browsing don't count. 0 = counts at start. Only applies together with `max_items_per_day` or `gem_cost` (§16). |
| `gem_cost` | int 1–20 or null, default null | Gems 💎 a **listen** of a file in the scope costs (§16): a new file may only start if the child has enough gems, and they are spent when the file counts (`count_after_seconds`). A listen lasts the rest of the day within `replay_percent`. null = no gems needed. |

### 3.2 Evaluation

A media item **may be started** if, for **every** enabled rule that covers the item's group, all of these hold. Conditions 1, 3 and 4 apply only if the rule is active **today**. Condition 2 follows the lockout window rules in §3.3b.

1. The rule's mode is not `block`.
2. The current time is not inside any of its lockout windows that are in effect (§3.3b).
3. If `max_items_per_day` is set, either the item **already counts today** for this rule or the scope's count of such items today is below the limit. An item counts once it has played for at least the rule's `count_after_seconds` today (§3.3). A counted item is also denied once its listening budget (`replay_percent` of its length) is used up today; this check is skipped where the item limit is (the stream endpoint).
4. If `max_minutes_per_day` is set, the scope's playback seconds today are below the limit.
5. If `gem_cost` is set, either the item **already counts today** for this rule (its gems are spent, §16) and its listening budget isn't used up, or the child's gem balance covers the costs of **all** gem rules that cover the item and don't count it yet (§16.2).

If no rule applies, the item is allowed with no limit.

**A file that is already playing** (heartbeat, stream) is evaluated for the day its session started (§3.3): that day's usage, and the item and minute limits of that day's weekday. Lockouts and `block` always follow the clock. So after midnight a running Friday video still stops at Friday's minute limit, and doesn't touch Saturday's, while a lockout or `block` that starts at 00:00 stops it as usual (see *Midnight boundary*).

**Remaining time.** For an allowed item the server also returns `remaining_seconds`: the minimum of (a) the minutes left in every applicable minute limit, plus the listening budget left for a counted item under every applicable `replay_percent`, and (b) the time until the next lockout window **that will actually be in effect** starts, in every applicable rule. A window only counts if its rule is active on the day the window starts (§3.3b). `null` means no limit. The app counts this down locally and pauses at zero.

**Midnight boundary.** Most of tomorrow's rules can be ignored, because a new day resets the evaluation. But something that **denies right at 00:00** would otherwise stop playback abruptly, with no countdown. Examples are a lockout window that starts at 00:00 on a day its rule is active, or a `block` rule that is active tomorrow and not today. So the server also evaluates the current item **as a fresh start at tomorrow 00:00**, with tomorrow's weekday and empty usage. If that evaluation denies because of a lockout or a `block`, `remaining_seconds` is also capped at the seconds until midnight.

That evaluation ignores item and minute limits, which reset at midnight and can't deny at 00:00 with empty usage. It also ignores the morning part of a window that is already running, because that is already counted in (b). Grace (§3.3a) applies at the midnight boundary exactly as at any other lockout start; a `block` never grants grace. Windows that start later tomorrow are still ignored: the next day's evaluation picks them up.

The **first denying rule** gives the child-facing reason. Reasons, in priority order:

| Reason code | Child message (example) |
|---|---|
| `blocked_today` | "Not today — back on Saturday" |
| `lockout` | "Sleeping time until 7:00" |
| `item_limit` | "You've had your story for today" |
| `minute_limit` | "Time's up for today" |
| `gems` | "You need 2 💎 — you have 1" (params `need`, `have`, §16) |

The server returns a **reason code plus parameters** (e.g. `{"reason":"lockout","until":"07:00"}`, `{"reason":"blocked_today","next_day":5}`), never finished sentences. The app turns them into child-friendly text in the tablet's language (English, German or Greek). **Custom messages (M6):** a rule may carry the parent's own text in each language (`message_en`, `message_de`, `message_el`, up to 140 characters). When that rule gives the reason, the server adds them to the params unchanged (`{"reason":"item_limit","limit":1,"message_en":"One story a day!"}`). The app shows the text for its language and falls back to its built-in text for the code. The reason code itself never changes.

### 3.3 Counting

- **Items:** a `play` row `(profile, media_id, day)` is created when the app calls `POST /api/play/start` and the item is allowed. It **uses up an item** of a rule only once its playing seconds that day (`plays.seconds`, summed over all sessions) reach the rule's `count_after_seconds`. A quick tap stays in the activity log but doesn't count, and stopping at 9 s to start again doesn't reset it. Once the limit is used up, a file that was only tapped is a new file again and is denied. Starting a counted file again that day, or seeking or resuming in it, never counts again.
  - **Listening budget:** a counted file may play for `replay_percent` of its length per day in total (`plays.seconds`), e.g. 36 min for a 30-min episode at 120 %. That leaves room for pausing, a dropout or rewinding a bit, but not for listening to the whole story again. Rewinding to the start doesn't help, because the seconds keep adding up. When the budget is used up, the countdown stops playback and the file is denied with `item_limit`, even if other items are still free. No grace (decision 14). `replay_percent = null` keeps the old "replays are free" behaviour. **No length, no budget:** if a file's length is unknown, its replays aren't limited. On the server that only happens for deleted media (which can't be played) or a missing `duration_s`. Offline, the app takes lengths from its cached library; a sync stores the library before the rules snapshot, so whenever there is a snapshot to enforce, there are lengths too.
  - Thresholds are per rule, so one play can already count for one rule and not yet for another.
  - The heartbeat books the time, so a file crosses the threshold at the next heartbeat (§3.3, every 15 s). With several tablets playing at once, a limit can be overshot by one; that is accepted.
- **Minutes: cumulative counter, not deltas.** Paused or buffering time does not count.
  - Each `POST /play/start` returns a new `play_session_id`.
  - While **actually playing**, the app sends a heartbeat every 15 s. It carries `played_seconds_total`: the playing time accumulated in this session, measured with the monotonic clock, so it can never go backwards.
  - **Final heartbeat:** before a session is replaced (auto-advance, tapping another item) or stopped, the app sends one last heartbeat for it, so the time since the previous heartbeat (up to 15 s per file) is counted. Its answer is ignored. The app waits for it at most 3 s before calling `/play/start` for the next item, so that start's minute check already includes it.
  - The server stores the last total per session and adds `delta = total − last_total` to the day's usage.
  - **Upper bound:** `delta` is capped at the wall-clock time since the session's previous heartbeat (or its start), plus 5 s of tolerance. A buggy or replayed request therefore can't add more time than really passed.
  - **Monotonic on the server too:** the stored total only ever grows. On each heartbeat the server sets `last_total_seconds = max(last_total_seconds, total)`, and it advances `last_heartbeat_at` **only when the total increased**.
    - A lower, equal or out-of-order total (for example a delayed retry) books nothing and changes nothing.
    - So a replayed old request can't lower the stored total, which would let the next real heartbeat book the same time twice. It also can't move `last_heartbeat_at` forward, which would shrink the cap window for the next real heartbeat.
    - The heartbeat is still answered normally (allowed, remaining, grace), so the app's state stays current.
  - **No loss when offline:** if heartbeats fail (network drop, server restart), the app keeps counting locally and keeps enforcing its own countdown (§4.3). The next successful heartbeat catches up the whole offline period, within the bound above. An earlier draft clamped each delta to 60 s, which would have quietly dropped offline playing time. It was dropped.
  - **Day boundary:** all of a session's time, including a catch-up that spans midnight, is booked to the session's day. Offline plays work the same way: `POST /play/batch` books each event to the day it started (M7).
  - **Unknown or pruned session:** a heartbeat whose `play_session_id` doesn't exist gets **404** `{"reason":"unknown_session"}`. That covers sessions older than 7 days, ones lost to a restore from backup, and ones from another device. The app discards the unsent total for that session, which books no time, and calls `POST /play/start` again. That re-evaluates the rules and returns a fresh session; if the start is denied, it shows the reason screen. A tablet offline for more than a week therefore loses at most that one stale session's uncounted time, which is acceptable.
- **Midnight rollover:** a session that runs past midnight stays on its own day: no new `play` row, and today's items and minutes are untouched. A story that started on Friday evening therefore doesn't use up Saturday's story. Lockouts and `block` at the new day still apply (with grace for lockouts, §3.3a), and the response tells the app to stop when they deny.
- **Grace counts.** Minutes played during grace (§3.3a) still add to the usage totals.
- **Deleted media keeps counting.** Each `plays` row stores `group_id` as a snapshot (§6.1). If a file is deleted during the day, its plays and minutes still count toward today's rules.

### 3.3a Grace ("finish the episode")

A minute limit or a starting lockout should not cut off the last few minutes of a story. Each rule therefore has `grace_minutes` (default 0, set per rule in the admin).

- **When it applies:** an item is **already playing** when that rule's minute limit runs out or one of its lockout windows begins.
- **Condition:** the rest of the current item (`duration − position`) is **≤ `grace_minutes`** of that rule.
- **Effect:** the item may play to its end. Seeking forward is allowed but doesn't extend the grace. Seeking back past the point where grace began ends the grace. After the item ends, auto-advance and new starts are denied as normal.
- **At most one grace per file per day.** Once a grace has ended (the file finished, the grace expired or the child seeked back), it isn't granted again for that file that day, so the wall-clock cap can't be restarted. A file counts as finished once its position is within 2 s of its duration, so no grace is granted that close to the end.
- **Grace never starts a file.** It only lets the item that is already playing finish. Every new file, including an auto-advance to the next item in the queue, is evaluated under the normal rules, item limit included, however short it is ([#3](https://github.com/docdru/kidsmedia/issues/3)).
- **Otherwise:** playback stops immediately, as without grace.
- **Server-side:** at the moment grace begins, the server stores a `graces` record `(profile, media_id, rule_id, granted_at, start_position_ms, until_position_ms, expires_at)`, where `expires_at = granted_at + 2 × grace_minutes` of wall time. That cap stops a child pausing and resuming forever.
- **The stream endpoint honours grace.** Mid-playback, ExoPlayer makes new `Range` requests when seeking, rebuffering or reopening the connection. `GET /api/media/{id}/stream` must allow a request when the normal check fails **only** because of a minute limit or a lockout, **if** an active grace for this profile and media exists (`expires_at > now`, not ended). The same applies to `POST /play/heartbeat`. A grace ends when a heartbeat reports `position_ms ≥ duration − 2 s`, when it expires, when a different item is started, or when a seek goes back before the position at which grace began (`start_position_ms`). A `block` rule or a revoked device is never bypassed by grace.
- **Multiple rules:** grace is decided per rule. If several rules expire at once, the item continues only if **every** expiring rule grants grace. Rules that are `block` mode never grant grace, because they only apply at start.
  - A grace covers only the rules that granted it (stored in `graces.rule_ids`). If **another** rule expires while it runs (bedtime starts during a music-limit grace), that rule must grant grace for the rest of the item on its own. If it does, it joins the grace, and the wall-clock cap becomes the earlier of the old cap and `now + 2 × its grace_minutes`. If it doesn't, the grace ends and playback stops. The stream endpoint applies the same check.
- **App:** shows a small "Finishing this episode" pill instead of the countdown, then the normal reason screen when the item ends.
- **Response fields:** heartbeat and start responses carry `grace: {active, until_position_ms, rule_id}`.

### 3.3b Lockout windows and weekdays ("evening-anchored")

A lockout window belongs to the weekday on which it **starts**. For a rule active on the set of days `D` and a window `S–E`:

- **Same-day window** (`S < E`, e.g. `12:00–13:30`): locked on day `d` from `S` to `E` if `d ∈ D`.
- **Window over midnight** (`S > E`, e.g. `19:30–07:00`), with two parts:
  - the **evening part** `S–24:00` is in effect on day `d` if `d ∈ D`;
  - the **morning part** `00:00–E` is in effect on day `d` if **the previous day** `d−1 ∈ D`.
- **`S = E`** is invalid and is rejected by the admin.

So one rule describes "school nights" naturally. `Sun–Thu 19:30–07:00` locks Sunday evening through Monday morning, and so on until Thursday night through Friday morning. Friday and Saturday nights stay free, and no morning is locked by a night the rule never covered.

Only lockouts are anchored this way. `block` mode always uses the **current** weekday. The item and minute limits use the current weekday for a new start, and the session's weekday for a file that is already playing (§3.2). Counting resets at midnight for new starts (§2 *Day*).

The admin rule editor shows windows over midnight with "(until next morning)". Its weekday pills read "Evenings starting on…" when the rule only has lockouts.

### 3.4 Worked examples (they double as acceptance tests)

| Intent | Configuration |
|---|---|
| One audiobook a day from *Moon Pirates* **or** *Detective Fox* (one total), weekends only | Rule A: scope {Moon Pirates, Detective Fox}, Sa+Su, limit, max_items=1. Rule B: same scope, Mo–Fr, block. |
| One from *each* series on weekends | Two rules, one per series, each max_items=1, Sa+Su. |
| Music up to 90 min a day | Rule: scope {Music}, all days, max_minutes=90. |
| Videos 30 min on weekdays, 60 at weekends | Two rules on {Cartoons}: Mo–Fr max_minutes=30, Sa+Su max_minutes=60. |
| Bedtime | Rule: scope all, all days, lockout 19:30–07:00. |
| No screens during lunch | Rule: scope {Cartoons}, all days, lockout 12:00–13:30. |
| Story may finish if ≤ 10 min are left at bedtime | Bedtime rule with `grace_minutes=10`. At 19:30 an episode with 8 min left plays to the end. One with 25 min left stops at 19:30. |
| …and seeking during grace still streams | Same rule, at 19:33 during grace: a seek triggers a new `Range` request, which gets **206** rather than 403. Once the episode has ended (about 19:38), or at the latest when the grace expires at 19:50 (2 × 10 min after it began), the request gets **403**. |
| School nights only | Rule: scope all, Sun–Thu, lockout 19:30–07:00. **Locked:** Sun 20:00, Mon 06:00, Thu 23:00, Fri 06:30. **Not locked:** Fri 20:00, Sat 06:00, Sat 20:00, Sun 06:00. |
| Weekend lie-in | Rule A: all, Sun–Thu, lockout 19:30–07:00. Rule B: all, Fri+Sat, lockout 20:30–08:30. Sat 08:00 is locked by B (Friday's night). Mon 07:30 is not locked (A's morning ended at 07:00). |
| Midnight-starting lockout gets a countdown | Rule: all, Sat only, lockout 00:00–06:00. Fri 23:50, playing music with no other limits: `remaining_seconds = 600` (to midnight), and at 00:00 playback stops with `lockout` (until 06:00). With `grace_minutes=10` and ≤ 10 min left in the song, it finishes. Same for a `block` rule active Sat only: Fri 23:50 → `remaining_seconds = 600`, and at 00:00 it stops with `blocked_today`, without grace. |
| Offline catch-up | Playing music, the network drops for 4 min, then a heartbeat arrives with `played_seconds_total` up by 240 s, 245 s after the previous heartbeat. 240 s are booked. A replayed or buggy total 1 000 s higher is capped at the elapsed time + 5 s. |
| Replayed heartbeat | Session total stored as 300 s. A delayed retry arrives with 285 s: nothing is booked, and the stored total and `last_heartbeat_at` stay unchanged. The next real heartbeat with 315 s books 15 s, not 30 s. |
| Grace doesn't start the next file | Stories rule max_items=1 with `grace_minutes=10`. Today's episode ends and the queue would auto-advance to a 2-minute bonus track. `/play/start` is denied with `item_limit`, and playback stops with the reason screen. |
| One grace per file per day | Bedtime rule with `grace_minutes=10`. At 19:30 an episode with 8 min left gets grace. The child seeks back to 13 min left, which ends the grace and stops playback. Seeking forward to 8 min left again does **not** grant a second grace that day. |
| Accidental taps | Rule: {Moon Pirates, Detective Fox}, Sa+Su, max_items=1, `count_after_seconds=10`. Sat: episodes 21 and 22 are tapped for 4 s and 9.9 s, so a third one may still start. Episode 23 plays 10 s: it counts and may go on, and 21, 22 and every other new file now get `item_limit`. Playing a file 9 s, stopping and playing it 6 s more also counts it (15 s that day). |
| Replay budget | Rule: {Moon Pirates}, Sa+Su, max_items=1, `replay_percent=120`. Sat: a 30-min episode played to the end has `remaining_seconds = 360` (6 min left of its 36). After 36 min in total it stops and gets `item_limit`, also when restarted. With `replay_percent = null` it may be replayed all day. |
| Gems unlock a listen | Rule G: {Cartoons}, all days, `gem_cost=2`, `count_after_seconds=10`, `replay_percent=120`. Balance 3 💎. A 10-min video starts (3 ≥ 2), plays 10 s: it counts and 2 💎 are spent (balance 1). It may go on and be replayed today up to 12 min in total, then `item_limit`. Another video can't start: `gems` {need 2, have 1}. With more gems, a tap of 5 s on another video spends nothing. Tomorrow the first video costs 2 💎 again. |
| Gems with other rules | Rule G as above plus the bedtime lockout and Videos max_minutes=30. All conditions hold together: enough gems don't open a lockout or extend minutes, and a spent listen still stops at 19:30 (grace as usual). |
| Gems: two rules on one group | Rules G (2 💎) and H (1 💎) both cover {Cartoons}. A new video needs 3 💎; at the count both spend (2 + 1). |
| Gems taken away while playing | Balance 2, a video starts (cost 2) and the parent removes 1 💎 after 5 s: the next heartbeat denies with `gems` {need 2, have 1}, before the video counts. After it counted, removing gems doesn't stop it. |
| Pruned session | A heartbeat for a session pruned after 7 days gets 404 `unknown_session`. Nothing is booked, and the app's next `/play/start` is evaluated normally. |
| Grace across midnight | Rule: all, all days, lockout 23:55–07:00, `grace_minutes=10`. An episode with 8 min left at 23:55 gets grace and keeps playing (and streaming) after 00:00. |
| Midnight rollover books to the session's day | The midnight-starting lockout example: the heartbeat at 00:00 books the last 600 s to Friday, and no Saturday `play` row is created, with or without grace. |
| A story running past midnight | Rule: all, all days, max_items=1. An episode started Fri 23:30 plays until 00:10. All 40 min are booked to Friday, and on Saturday another episode can still be started. |
| A video running past midnight | The weekday/weekend video rules (30 / 60 min). A video started Fri 23:40 keeps Friday's 30 min after midnight: at 00:05 `remaining_seconds = 300`, at 00:10 it stops with `minute_limit` (limit 30), and its stream gets 403. A new video started at 00:10 gets Saturday's 60 min. |
| A later rule needs its own grace | Rule A: {Music}, max_minutes=5, `grace_minutes=10`. Rule B: all, lockout 14:30–07:00. Song of 15 min, started 14:20. At 14:25 A runs out with 10 min left → grace. At 14:30 B starts with 5 min left: with B `grace_minutes=0` playback stops (`lockout`) and streaming gets 403. With B `grace_minutes=5` it continues, and the grace now expires at 14:40 (2 × 5 min after 14:30). |

### 3.5 Admin conveniences
- **"Right now" preview.** The rules page shows, for every group, what the child sees at this moment (available / reason / items and minutes left). It runs the same evaluation code.
- **Simulate.** Pick a weekday and time to preview the rules at that moment (P1).
- **Reset today.** Deletes today's plays and minutes. Useful as a reward or after a mistake.
- **Bonus.** Grant +N minutes or +1 item for today, stored as a one-day adjustment (P2).

---

## 4. Components and architecture

```
┌──────────────────── Linux server ─────────────────────────┐
│  host nginx (TLS via Let's Encrypt, existing)               │
│     │  proxy_pass → 127.0.0.1:8080                          │
│     │  internal /_media/ → alias /opt/kidsmedia/data/media/ │
│     ▼                                                       │
│  Docker: kidsmedia app (FastAPI)                            │
│     ├─ REST API (/api)      ├─ Admin SPA (/)                │
│     ├─ auth + rules check, then X-Accel-Redirect to nginx   │
│     └─ ffprobe on upload                                    │
│  /opt/kidsmedia/                                            │
│     ├─ docker-compose.yml  .env                             │
│     └─ data/ kidsmedia.db  media/…  thumbs/…  apk/…         │
└───────────────────────▲────────────────────────────────────┘
                        │ HTTPS, Bearer token
               ┌────────┴────────┐
               │ Android app      │  Kotlin · Compose · Media3
               └─────────────────┘
```

### 4.1 Server
- **Python 3.12, FastAPI, Uvicorn**, SQLAlchemy 2 + Alembic migrations, **SQLite** (WAL mode).
- The rules engine is a pure module (`rules.py`) with no I/O, so it is unit-tested exhaustively, including property tests with Hypothesis.
- **Host layout:** everything lives in `/opt/kidsmedia` (`docker-compose.yml`, `.env`, `data/`). The container mounts `/opt/kidsmedia/data` at `/data`. Media files are at `data/media/<group_id>/<uuid>.<ext>`, thumbnails and group covers at `data/thumbs/` (§6.4), app releases at `data/apk/` (uploaded, or imported from the image, §4.3).
- **Port:** the container publishes only on `127.0.0.1:8080`. The existing host nginx is the only public entry point.
- **Streaming:** `GET /api/media/{id}/stream` checks the token and the rules (except the item limit, and honouring an active grace; see §3.3a and §5.3). It then answers with `X-Accel-Redirect: /_media/<group>/<file>`, and **nginx serves the bytes**: Range/206, sendfile, ETag and no Python in the data path. If `ACCEL_REDIRECT=false` (dev, CI, no nginx), the app streams with Range itself.
- **Uploads:** streamed straight to disk (`PUT` with a raw body, one file per request, no multipart buffering). Then `ffprobe` reads duration, codecs and title tags, and the thumbnail pipeline (§6.4) runs as a background job. The upload response returns right away with `thumb.status = "pending"`. Max size is configurable (default 4 GB).
- **Config via env (`/opt/kidsmedia/.env`):** `TZ`, `ADMIN_USER`, `ADMIN_PASSWORD` (first-run bootstrap; stored as an argon2 hash), `PUBLIC_URL`, `MAX_UPLOAD_MB`, `DATA_DIR`, `ACCEL_REDIRECT`, `DEFAULT_LOCALE` (`en`|`de`|`el`).
- **Image:** multi-stage Dockerfile on `python:3.12-slim` with `ffmpeg`, a non-root user (UID/GID configurable so files in `/opt/kidsmedia/data` are readable by nginx), and a `HEALTHCHECK` on `/healthz`. Published to GHCR as `ghcr.io/docdru/kidsmedia` (public, no APK). The private family image builds on it and adds the signed APK of the same tag in `/app/apk-bundle` (§4.3, §18).
- **nginx site** (shipped as `deploy/nginx/kidsmedia.conf`, added into the existing server block or as its own `server_name`):

  ```nginx
  client_max_body_size 4g;

  location / {
      proxy_pass http://127.0.0.1:8080;
      proxy_set_header Host $host;
      proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
      proxy_set_header X-Forwarded-Proto $scheme;
      proxy_request_buffering off;      # stream uploads straight through
      proxy_read_timeout 3600s;
  }

  location /_media/ {                  # only reachable via X-Accel-Redirect
      internal;
      alias /opt/kidsmedia/data/media/;
      add_header Cache-Control "private, max-age=86400";
  }
  ```

### 4.2 Admin web app
- **React + TypeScript + Vite**, built into static files that FastAPI serves at `/`. TanStack Query for data. API types are generated from the server's OpenAPI schema (`openapi-typescript`).
- Desktop-first and usable on a phone, so a parent can tweak rules from the couch.
- Session cookie login (HttpOnly, SameSite=Strict, CSRF token on mutations).
- **i18n:** `react-i18next` with `en`, `de` and `el` catalogs. Language follows the browser, with a switch in the header that is remembered per admin user. Other browser languages fall back to `DEFAULT_LOCALE`. CI fails on missing keys in any language.

### 4.3 Android app
- **Kotlin, Jetpack Compose, Material 3**, minSdk 26, target the current SDK.
- **Media3:** ExoPlayer inside a `MediaSessionService` (background audio, lock-screen and notification controls). UI uses a `MediaController`. `OkHttpDataSource` adds the `Authorization` header.
- **Buffering** with a custom `DefaultLoadControl`:

  | | min buffer | max buffer | start playback | back buffer |
  |---|---|---|---|---|
  | Audio | 60 s | 10 min | 2.5 s | 60 s |
  | Video | 30 s | 3 min | 2.5 s | 30 s |

  Plus OkHttp retries and Media3 `LoadErrorHandlingPolicy` with exponential retry for at least 60 s before an error is shown.
- **Networking:** Retrofit + kotlinx.serialization, with the client generated from (or checked against) OpenAPI.
- **Images:** Coil with the auth header.
- **Storage:** DataStore for server URL, token and parent PIN (hashed).
- **i18n:** all strings in `res/values/strings.xml` (English), `res/values-de/strings.xml` (German) and `res/values-el/strings.xml` (Greek). The app follows the system language until the child taps a language flag on Home (§14.7). The choice is the per-app language (Android 13+ `LocaleManager`, older versions a stored choice applied to the activity's context). There is no language setting behind the parent PIN. Reason codes from the server map to localized strings. Android Lint `MissingTranslation` runs as an error in CI.
- **Enforcement:** see §5.3. The local countdown pauses playback when `remaining_seconds` reaches 0 even if the server is unreachable.
- **Volume cap (§5.1):** the app clamps Android's music stream (`STREAM_MUSIC`) to `floor(max_index × percent / 100)`, at least one step. It clamps when the cap changes, on every volume change, when the output changes (Android keeps separate levels for the speaker and headphones), and once a second while the playback service runs. At the cap, the volume-up key is swallowed while the app is in front. Clamping the system stream rather than scaling the player keeps the system slider honest. Limits: it only works while the app's process runs (true with screen pinning), and not on devices with fixed volume.
  - If that happens offline, the app shows the reason from its offline rules snapshot (M7). Without one it shows a neutral "Let's take a break" (app-side code `pause`), never a guessed reason.
  - A forced heartbeat at zero is queued if another heartbeat is still in flight, so the reason screen always appears.
- **Offline time:** at each sync the app anchors the server's time (`server_time` of the library) to the monotonic since-boot clock. Offline rules and offline plays use that anchored time, so changing the tablet's clock doesn't move lockouts or the day. After a reboot the anchor is gone, and the tablet's own clock is used until the next sync. This fails gracefully for the child: offline playback keeps working.
- **Parent gate:** a long press (2 s) on the small settings dot opens a 4-digit PIN pad. Behind it: server URL, re-pair, app version, "check for update". Exiting the app is handled by Android screen pinning or Family Link, which the README documents.
- **Self-update (P1):** server and tablet stay on the same release without anyone carrying an APK around.
  - **Where releases come from:** each family image (§18) carries the signed APK built from its tag (`/app/apk-bundle`: the APK plus `release.json` with `version_name`, `version_code`, `file`). On every start the server imports it into `data/apk/` and `app_releases`, unless that `version_code` is already known, so restarts do nothing. The parent can still upload an APK in the admin. The newest `version_code` wins, the newest 3 are kept, and an older image doesn't take a newer app away. Images built from `main` and the public release images carry no APK.
  - **API:** the app checks `GET /api/app/latest` (`{version_name, version_code, size, sha256, url}`, or 404 `no_release`), downloads `GET /api/app/apk` (device token, X-Accel-Redirect from `/_apk/`) and checks the SHA-256 before installing via `PackageInstaller`. Android itself refuses an APK signed with a different key.
  - **Automatic:** on Android 12+ the app checks by itself every 6 hours while the UI is visible and nothing plays, and installs without a confirmation (`setRequireUserAction(USER_ACTION_NOT_REQUIRED)`, permission `UPDATE_PACKAGES_WITHOUT_USER_ACTION`; Android allows this for an app updating itself that targets API 29+). The app restarts after the update. If Android still asks for a confirmation, the automatic update is dropped rather than showing a system dialog to the child. The time of the last check and the last version an automatic update was committed for are stored on the tablet, so they survive the app being killed. If the app is still older than that version afterwards, Android didn't install it: that version isn't downloaded again automatically and waits for "Check for update" in the parent settings, and the next newer release is tried again.
  - **By hand:** "Check for update" behind the parent gate works on every Android version. Below Android 12, Android asks the parent to confirm.

---

## 5. API (v1)

All JSON. Device endpoints need `Authorization: Bearer <device token>`. Admin endpoints need a session cookie. The OpenAPI schema is committed at `openapi.json`, and CI fails if it drifts from the code.

### 5.1 Pairing
| Method | Path | Body → Response |
|---|---|---|
| POST | `/api/admin/devices` | `{name}` → `{id, pairing_code, expires_at}` (6 digits, valid 15 min) |
| POST | `/api/pair` | `{code, device_name}` → `{token, device_id}` |
| PATCH | `/api/admin/devices/{id}` | `{name?, max_volume_percent?}`. Renames the tablet and/or sets its volume cap (5–100, `null` = no cap). A field left out is unchanged. |
| DELETE | `/api/admin/devices/{id}` | Revokes the token. |

**Volume cap.** Each tablet has an optional `max_volume_percent`, set by the parent per tablet (each tablet has its own speaker). `GET /api/library` and `GET /api/me` return it. The app saves it, so it also holds after a restart and while offline, and enforces it as in §4.3. Re-pairing keeps the saved cap; only the server lifts it.

### 5.2 Library (device)
`GET /api/library` →
```json
{
  "server_time": "2026-11-07T14:20:00+01:00",
  "groups": [{
    "id": 3, "name": "The Moon Pirates", "kind": "audiobook", "color": "#4552C7",
    "cover": {"url": "/api/thumbs/g3-9f2c1a.webp", "aspect": 1.0},
    "status": {"allowed": false, "reason": "item_limit", "params": {"limit": 1},
               "items_left": 0, "minutes_left": null, "unlocks_at": null},
    "items": [{"id": 41, "title": "Episode 4: The Storm", "duration_s": 2710,
               "thumb": {"url": "/api/thumbs/m41-4be07d.webp", "aspect": 1.0, "source": "embedded"},
               "position_ms": 812000,
               "played_today": true, "allowed": true}]
  }]
}
```
`status` describes whether a **new** item from the group can be started. Each item's `allowed` also counts `played_today`.

### 5.3 Playback (device)
| Method | Path | Notes |
|---|---|---|
| POST | `/api/play/start` | `{media_id}` → `{allowed, reason, params, remaining_seconds, grace, play_session_id, position_ms, stream_url}`. Creates the `play` row (if new today) and a play session when allowed. `position_ms` is 0 for groups with `resume = false`. |
| POST | `/api/play/heartbeat` | `{media_id, play_session_id, played_seconds_total, position_ms}` → `{allowed, reason, params, remaining_seconds, grace}`. Counting works as in §3.3. `allowed:false` means pause now and show the localized reason screen. Unknown session → **404** `unknown_session`, and the app calls `/play/start` again. |
| GET | `/api/media/{id}/stream` | Range streaming. Returns 403 + reason when blocked, locked or out of minutes, **unless an active grace covers this media** (§3.3a). The item limit is **not** checked here, because ExoPlayer preloads the next queue item before its start call. |
| GET | `/api/thumbs/{key}.webp?size=sm\|md\|lg` | Thumbnails and group covers. The key contains a content hash, so responses are `Cache-Control: immutable`, and a changed picture gets a new URL. Requires the device token. Served by nginx through `X-Accel-Redirect` like media. |

**Queue behaviour:** music groups play the whole group from the tapped item onward, with optional shuffle. Audiobook and video groups also queue the following items, but each auto-advance calls `/play/start` first. If that is denied, playback pauses and the reason screen appears.

**Resume:** controlled by the group setting `resume`. It defaults to `true` for `audiobook` and `video` groups and `false` for `music`, and the parent can change it in the admin.
- `resume = true`: the heartbeat's `position_ms` is stored in `positions`, and `/play/start` returns it. Positions within the last 3 % of an item are stored as 0, so a finished episode starts from the beginning next time.
- `resume = false`: the server **does not persist** positions for the group. It ignores the heartbeat's `position_ms`, apart from grace bookkeeping, and `/play/start` always returns 0. Pausing and resuming within a running session is handled by the player and doesn't involve the server.
- Switching a group from `true` to `false` deletes its stored positions.

### 5.3a Offline (M7)
- `GET /api/offline/snapshot` → `{server_time, time_zone, group_ids, rules[], usage{day, started{media_id: group_id}, seconds{group_id: s}, extra_items{rule_id}, extra_minutes{rule_id}}}`. Only enabled rules of the device's profile. The app runs the same rules engine (§3) on it while offline.
- `GET /api/media/{id}/download` → the whole file for the offline copy, only for items marked offline (403 `not_offline`). Downloading is not playing, so no rule check; plays count when they happen.
- `POST /api/play/batch {events:[{event_id, media_id, started_at (with offset), seconds, position_ms}]}` → `{accepted, duplicates, unknown_media}`. Each `event_id` is booked once. The day comes from `started_at` in the server's TZ; a `started_at` in the future is pulled back to now; one event books at most 6 h. Offline plays count towards item and minute limits like online ones.
- The library marks `offline` on groups and items and gives each item's `size`. Admin: `PATCH /api/admin/groups/{id} {offline}` and `PATCH /api/admin/media/{id} {offline}`.

### 5.4 Admin
- `GET /api/admin/state` — groups, media, rules, devices, today's usage.
- Groups: `POST /api/admin/groups`, `PATCH|DELETE /api/admin/groups/{id}`, `POST /api/admin/groups/reorder`.
- Group cover: `PUT /api/admin/groups/{id}/cover` (image body), `POST /api/admin/groups/{id}/cover {source:"item", media_id}`, `DELETE …/cover` (back to automatic: first item's thumbnail).
- Media: `PUT /api/admin/groups/{id}/upload?filename=…` (raw body), `PATCH|DELETE /api/admin/media/{id}` (title, group), `POST /api/admin/media/reorder`.
- Thumbnails:
  - `GET /api/admin/media/{id}/frames?count=8` returns evenly spaced preview frames for a video.
  - `GET /api/admin/media/{id}/frame?t_ms=…` returns one frame as JPEG for scrubbing.
  - `POST /api/admin/media/{id}/thumb {source:"embedded"|"frame", t_ms?}` sets the thumbnail.
  - `PUT /api/admin/media/{id}/thumb` sets an uploaded image.
  - `DELETE /api/admin/media/{id}/thumb` re-runs the automatic choice.
  - `POST /api/admin/groups/{id}/thumbs/apply {source:"upload-of", media_id}` uses one image for every item in the group. This is handy for audiobook series without embedded art.
- Rules: `POST /api/admin/rules`, `PATCH|DELETE /api/admin/rules/{id}`, `GET /api/admin/preview?at=2026-11-07T14:20`.
- Usage: `GET /api/admin/usage?day=`, `GET /api/admin/usage/history?days=7`, `POST /api/admin/usage/reset-today`, `POST /api/admin/usage/bonus {rule_id, extra_items, extra_minutes}` (adds to that rule's limits for today only).
- App updates: `PUT /api/admin/app/apk?version_name=&version_code=` (raw APK body, must be newer than the latest; the newest 3 are kept), `GET /api/admin/app`.
- `GET /healthz`.

---

## 6. Data

### 6.1 Schema (SQLite)
v1 creates exactly one profile and the UI never shows it, but every usage table and rule carries `profile_id`. Multi-child support (M8) then needs only UI work, a profile picker on the tablet, and devices or rules bound to profiles.

```
profiles(id, name, locale, created_at)                    -- v1: one row
groups(id, name, kind, color, resume BOOL (default: kind != 'music'),
       cover_source 'auto'|'item'|'upload', cover_media_id → media ON DELETE SET NULL,
       cover_key, cover_aspect (uploads only), sort, created_at)
admin_sessions(id, user_id → admin_users, token_hash, csrf_token, created_at, expires_at)
media(id, group_id→groups, title, path, mime, size, duration_s, width, height, faststart, has_embedded_art, sort, created_at,
      thumb_source 'pending'|'embedded'|'frame'|'upload'|'none', thumb_key, thumb_aspect, thumb_frame_ms)
rules(id, profile_id NULL = all profiles, name, enabled, days TEXT '0123456', all_groups, mode,
      max_items, max_minutes, lockouts JSON, grace_minutes DEFAULT 0, count_after_seconds DEFAULT 10, replay_percent NULL DEFAULT 120, gem_cost NULL (§16),
      message_en NULL, message_de NULL, message_el NULL)              -- parent's own reason text (§3.2)
graces(id, profile_id, media_id, rule_id, rule_ids JSON, day, granted_at, start_position_ms, until_position_ms, expires_at, ended_at)
rule_groups(rule_id→rules, group_id→groups)            PK(rule_id, group_id)
plays(id, profile_id DEFAULT 1, media_id → media ON DELETE SET NULL, group_id (snapshot), title (snapshot),
      day DATE, seconds REAL, started_at, last_at)       UNIQUE(profile_id, media_id, day)
play_sessions(id, profile_id, device_id, media_id, day, started_at, last_heartbeat_at,
              last_total_seconds)                        -- §3.3: total only grows; pruned after 7 days (heartbeat → 404)
positions(profile_id, media_id, position_ms, updated_at) PK(profile_id, media_id)
devices(id, profile_id, name, token_hash, locale, created_at, last_seen_at, revoked_at,
        max_volume_percent NULL = no cap)
pairing_codes(code, device_id, expires_at)
admin_users(id, username, password_hash)
adjustments(id, profile_id, day, rule_id, extra_items, extra_minutes)   -- bonus for one day (§3.5)
gem_ledger(id, profile_id, delta, kind, rule_id NULL, media_id NULL, day NULL, title, note, created_at)  -- §16.1
app_releases(id, version_name, version_code UNIQUE, file, size, sha256, uploaded_at)   -- self-update; newest 3 kept
offline_events(event_id PK, profile_id, received_at)   -- M7: de-duplicates offline plays
-- M7: groups.offline and media.offline (BOOLEAN DEFAULT 0) mark what tablets keep a copy of
```
Deleting a group deletes its media files.

**Deleting a media item:**
- `plays` history is kept: `media_id` becomes NULL, and the `group_id` and `title` snapshots stay. Today's usage still counts (§3.3).
- Its thumbnail files are deleted.
- **Group cover fallback:** when the item was the group's chosen cover (`cover_source = 'item'`), the same transaction sets `cover_source = 'auto'` (the FK also sets `cover_media_id` to NULL). Readers also treat `cover_source = 'item'` with a NULL `cover_media_id` as `auto`, as a safety net.
- **Covers are resolved at read time.** `auto` uses the first item (by sort) that has a thumbnail, and `item` uses that item's thumbnail; both reuse the item's content-hashed key. `cover_key`/`cover_aspect` are only stored for **uploaded** covers. Deleting or reordering items therefore yields the new cover URL automatically, with no stored key to recompute.

### 6.2 Backups
The README documents `sqlite3 /opt/kidsmedia/data/kidsmedia.db ".backup …"` plus rsync of `/opt/kidsmedia/data/media`. Nothing else holds state.

### 6.3 Supported formats
- Audio: mp3, m4a/AAC, ogg/opus.
- Video: mp4 (H.264 + AAC, up to 1080p).
- Upload rejects other codecs with a clear message and an `ffmpeg` command line to convert. For progressive streaming the server checks the `moov` atom is at the front of mp4 files and offers a one-click "optimize" (`ffmpeg -c copy -movflags +faststart`, no re-encode).

### 6.4 Thumbnails

**Automatic choice after upload.** The first source that works wins:

| # | File type | Source | How |
|---|---|---|---|
| 1 | mp3 | Embedded ID3 picture (`APIC`, front cover preferred) | `ffmpeg -i in -map 0:v:0 -frames:v 1` on the attached-picture stream (`disposition.attached_pic`) |
| 1 | m4a / mp4 | Embedded cover (`covr` atom) | Same: attached-picture stream |
| 1 | ogg / opus | `METADATA_BLOCK_PICTURE` | Same |
| 1b | mp3, m4a/mp4, ogg/opus | The same pictures, read **from the tags** | Only if ffmpeg found none, or its picture didn't decode: the tags are read with `mutagen` (ID3 `APIC`, front cover first; `covr`; `METADATA_BLOCK_PICTURE`) and Pillow recognises the image by its bytes. ffmpeg skips an `APIC` frame whose MIME type is empty or unknown, which some taggers write (e.g. fre:ac), while macOS shows it. The admin's *From file* option (`has_embedded_art`) counts these pictures too. |
| 2 | mp4 (video) | A representative frame | `ffmpeg -ss <10 % of duration, at least 3 s> -i in -vf "thumbnail=120,scale=…" -frames:v 1`. The `thumbnail` filter picks the most typical of 120 frames, which avoids black fades and title cards. If the result is almost black or a single colour (mean luma < 16 or very low variance), try 25 % and then 50 %. |
| 3 | any | Nothing found | `thumb_source = "none"`. Clients show the **group cover**. If that is missing too, they show a generated placeholder (group colour plus kind icon), as in the prototype. |

**Group cover:** automatic is the first item's thumbnail (by sort order). The parent can choose another item's thumbnail or upload an image.

**Output.** Images are decoded with Pillow (decompression-bomb limit 50 MP), EXIF-rotated, stripped of metadata, and written as **WebP** in three sizes:

| Size | Long edge | Used for |
|---|---|---|
| `sm` | 160 px | List rows, admin table, notification |
| `md` | 480 px | Home tiles, player on phones |
| `lg` | 1024 px | Player on tablets, video poster |

Audio art keeps its aspect ratio, usually square. Video frames keep 16:9. The tablet crops square tiles with `ContentScale.Crop`, so no cropped copies are stored. Files go to `data/thumbs/<key>-<size>.webp`, where `key` = `m<media_id>-<sha256[:6]>` or `g<group_id>-<sha256[:6]>`. Old files are deleted when replaced.

**Parent overrides** (admin → Library → a file → "Thumbnail"):
- *From file:* the embedded picture, if one exists.
- *Video frame:* a strip of 8 frames plus a scrub slider, with a live preview from `/frame?t_ms=`.
- *Upload image:* jpg, png or webp up to 20 MB, processed as above.
- *Automatic:* resets to the rules above.
- *Use for whole group:* applies one uploaded picture to every item without art (`thumb_source = 'none'`); items with embedded art, a chosen frame or their own upload keep it.

**Android:**
- Coil (with the auth header) loads `sm`, `md` or `lg` to fit the view and keeps a 100 MB disk cache. Because thumbnail URLs are immutable, that cache never needs revalidating.
- The player passes `lg` as `MediaMetadata.artworkUri`, so the notification and lock screen show the picture.
- Video shows `lg` as a poster until the first frame renders.
- M7 (offline) downloads `md` and `lg` along with the media.

**Tests (CI):** fixture files are generated in the test with `ffmpeg -f lavfi`: mp3 with and without `APIC`, mp3 with an `APIC` frame whose MIME type is empty (written by hand), m4a with `covr`, mp4 with a cover atom, mp4 with a black intro and then colour bars, and a corrupt image in `APIC`. Assertions cover the chosen source, the output sizes, the black-intro skip, and that a corrupt picture falls back to a frame or none without failing the upload.

---

## 7. Security
- HTTPS required in production. The app refuses `http://` except for private IP ranges (debug convenience).
- Device tokens: 256-bit random, stored as SHA-256 hashes, revocable, `last_seen_at` tracked.
- Admin: argon2id password hash, login rate limit, session expiry 30 days, CSRF protection.
- Uploads: filenames never reach the filesystem path (UUID names). Size limit. MIME type is sniffed, not trusted.
- Brute force: `/api/pair` is limited to 5 attempts per minute per IP, and pairing codes are single-use.

---

## 8. Child UI (Android) — screens
Matches the clickable prototype *"Kids Media — Tablet app"*.

The app runs full screen: the status bar, the navigation bar and a tablet's taskbar are hidden (a swipe from the edge shows them for a moment), and content keeps clear of anything that stays covered (a camera cutout). Every screen works in landscape and portrait; below 840 dp of width the story screen (§13.7) stacks its parts and shows the activities as a 2 × 2 grid.

1. **Home:** a header with the greeting, the language button (§14.7), the stars (§14.4) and the gems (§16.4) — on a narrow screen (< 760 dp, a tablet held upright) the buttons go on their own line under the greeting — then a grid of large group tiles (group cover picture, name, colour bar). A badge on each tile shows "1 story left", "40 min left", "Locked until 7:00" or "Not today". Locked tiles are dimmed with a lock icon and stay tappable to show the reason.
2. **Group:** a header with the group cover, then a list of items with thumbnail (a 16:9 thumbnail for videos), title and duration, a progress bar when partly played, and a "played today" tick. When the group's item limit is used up, already-played items stay playable and the others show a lock.
3. **Player:** big thumbnail (`lg`; a poster frame for video), title, play/pause (≥96 dp), back 15 s / forward 30 s, a seek bar (tap or drag to jump; seeking during grace follows §3.3a), and a "time left today" pill, which reads "Finishing this episode" during grace. Video has a round full-screen button (72 dp, four corner brackets) in its top corner: the video then fills the screen, with tap-to-show controls, a leave button (inward brackets) top left and the time-left pill and gems top right. The leave button or the back gesture returns to the player; full screen holds no rule state, so blocks and grace work as before. A buffering spinner shows over the cover or video.
4. **Reason screen:** a friendly full-screen message ("Time's up for today", "Sleeping time until 7:00") with a single "OK" button back to Home.
5. **Parent gate:** long press on the settings dot opens the PIN pad, then Settings (server, device name, volume cap, pairing state, version, update).
6. **First run:** enter server URL, then the 6-digit pairing code, then set a parent PIN.

Accessibility: touch targets ≥ 64 dp, no text-only affordances, icons plus colour plus shape for states.

## 9. Parent admin — screens
Matches the clickable prototype *"Kids Media — Parent admin"*.

1. **Library:** a group list with a "new group" button. The selected group shows a drag-and-drop upload area (multiple files, per-file progress) and a sortable media list with a thumbnail column, title edit, move to group and delete. Each row's thumbnail opens the thumbnail picker (§6.4): from file, video frame strip plus scrubber, upload, or automatic. Rows still being processed show a "Making thumbnail…" placeholder. The group header shows the group cover with "Change cover".
2. **Rules:** rule cards with a plain-language summary ("Max 1 file per day from Moon Pirates + Detective Fox · Sat, Sun"). The editor has scope checkboxes, weekday pills, mode, limits and lockout windows. A "Right now" panel shows what the child can play.
3. **Devices:** paired tablets with last seen, a "Max volume" picker (no limit, 90 % … 20 %) and revoke, and "Pair new tablet" showing a 6-digit code and countdown.
4. **Activity:** today per group (items used/limit, minutes used/limit), a play log, "Reset today", and a 7-day history.

---

## 10. Repository and CI

### 10.1 Layout (monorepo)
```
/server           FastAPI app, Alembic, tests (pytest)
/admin            React + Vite admin SPA
/android          Gradle project (app module)
/deploy           docker-compose.yml, .env.example, nginx/kidsmedia.conf, install.sh (→ /opt/kidsmedia)
/docs             SPEC.md, ADRs, screenshots
openapi.json      generated, committed, checked in CI
```

### 10.2 GitHub Actions
| Workflow | Trigger | Steps |
|---|---|---|
| `server.yml` | PR / push touching `server/**` | ruff, mypy, pytest with coverage (rules engine ≥ 95 %), OpenAPI drift check |
| `admin.yml` | `admin/**` | pnpm install, eslint, tsc, vitest, build |
| `android.yml` | `android/**` | ktlint/detekt, unit tests, `assembleDebug`, upload the debug APK as a workflow artifact |
| `docker.yml` | push to `main` | build the multi-arch image (amd64, arm64) with the admin build embedded, push to `ghcr.io/<owner>/kidsmedia:{sha,latest}`. No APK inside. |
| `release.yml` | tag `v*` | the multi-arch image without an APK, pushed as `ghcr.io/<owner>/kidsmedia:{sha,semver}`, and a GitHub Release with the changelog. The signed APK is built by the family repository from the same tag (§4.3, §18) |
| `e2e.yml` | nightly (03:17 UTC), manual, PRs labelled `e2e` | build the image, install it with `deploy/install.sh` into `/opt/kidsmedia` behind the runner's nginx with TLS and the shipped snippet, then run `server/e2e/` (pytest + httpx over HTTPS): login + CSRF, uploads through nginx, pairing, Range streaming and thumbnails via `X-Accel-Redirect`, internal locations not public, time-independent rules (item limit, block), revoking. The §3.4 examples themselves are covered with a frozen clock in `server/tests/` |

Plus Dependabot (pip, npm, gradle, actions), branch protection on `main` (required checks), Conventional Commits, and release-please for versioning.

### 10.3 Testing strategy
- **Rules engine:** table-driven tests for every §3.4 example, edge cases (midnight-crossing lockouts, rollover during playback, overlapping rules, disabled rules, no rules), and Hypothesis property tests (e.g. adding a rule never makes something more allowed).
- **API:** pytest + httpx `AsyncClient` with a frozen clock (`time-machine`).
- **Android:** unit tests for the countdown and heartbeat logic with a fake clock and fake API, a Compose UI test for the home tile states, and a MockWebServer test that the stream request carries the auth header.

---

## 11. Milestones

| # | Scope | Done when |
|---|---|---|
| M0 | Repo, CI skeletons, Docker image builds, `/healthz` | All workflows green on an empty app |
| M1 | Server: groups, upload, automatic thumbnails, stream, device pairing, library API | `curl` can pair, list and stream with Range |
| M2 | Rules engine + start/heartbeat + admin rules API | All §3.4 examples pass as tests |
| M3 | Android: pairing, home, group, player (background audio, buffering, countdown) | Plays on the tablet over 4G with the screen off; limits enforced |
| M4 | Admin SPA: library, rules, devices, activity | A parent can do everything without curl |
| M5 | Hardening: HTTPS-only, thumbnail picker and group covers in the admin, resume, reason screens, parent gate, release signing | v1.0.0 tag produces an image and a signed APK |
| M6 (P1/P2) | Self-update (APK in the release image, silent on Android 12+), simulate, bonus time, custom reason texts (en/de), history charts | — |
| M7 | **Offline mode:** parent marks groups or items "available offline". The app downloads them into app-private storage (`/api/media/{id}/download`) and enforces the rules locally with a rules snapshot from the server and a Kotlin port of the engine (decision 16). Offline plays and minutes are queued and synced. The server accepts `POST /api/play/batch` and de-duplicates by client event id. | Plays a downloaded episode in flight mode, limits hold, usage appears in the admin after reconnect |
| M8 | **Several children:** profile management in the admin, rules per profile or shared, a profile picker or a device-to-profile binding on the tablet | Two children with separate limits on one or two tablets |

---

## 12. Decisions log

| # | Question | Decision |
|---|---|---|
| 1 | Profiles in v1? | One child in v1. The schema is profile-aware (`profile_id` everywhere) so more children arrive in M8 without reworking the data. |
| 2 | Offline playback? | Yes, later (M7). Not in v1. |
| 3 | "Finish the episode" grace? | Yes, in v1: per-rule `grace_minutes` (§3.3a). |
| 4 | Languages? | English and German from day one, Greek since v0.12: app, admin, child messages and story packs (§3.2, §4.2, §4.3, §13). Missing texts fall back to English, then German, then Greek. |
| 5 | Reverse proxy? | Existing host nginx with Let's Encrypt. No Caddy. App and data in `/opt/kidsmedia`. nginx serves media through `X-Accel-Redirect` (§4.1). |
| 6 | Lockouts over midnight vs. weekdays? | Evening-anchored: a window belongs to the day it starts (§3.3b). |
| 7 | Stream requests during grace? | Allowed while the grace is active (§3.3a). |
| 8 | Heartbeat counting? | Cumulative per-session total, capped by elapsed wall time. No 60 s clamp, so offline time is caught up (§3.3). |
| 9 | Music positions? | Per-group `resume` flag. Not persisted for music by default (§5.3). |
| 10 | Group cover when its media is deleted? | Falls back to `auto` (§6.1). |
| 11 | Lockout or block starting at midnight? | Counted down: `remaining_seconds` is capped at midnight when tomorrow 00:00 denies (§3.2). |
| 12 | Out-of-order or replayed heartbeats; pruned sessions? | Stored total = max(old, new). Pruned or unknown session → 404, and the app starts a new session (§3.3). |
| 13 | Admin language default? ([#2](https://github.com/docdru/kidsmedia/issues/2)) | Follow the browser (`en`/`de`/`el`), with a switch in the header that is remembered per admin user (§4.2). `DEFAULT_LOCALE` is only the fallback for other browser languages. |
| 14 | Does grace apply to the item limit? ([#3](https://github.com/docdru/kidsmedia/issues/3)) | No. Grace only lets the playing file finish, and new files (including auto-advance) always start under the normal rules (§3.3a). |
| 15 | How do custom reason texts fit "codes, not sentences"? | The code stays the contract. The parent's own text travels as params (`message_en`, `message_de`, `message_el`) and only replaces the app's built-in text for that code (§3.2). |
| 16 | How are offline copies stored? | Plain files in the app's private storage, fetched with the device token after each successful library refresh, and deleted when no longer marked offline. Other apps can't read them, so Media3's `DownloadManager` and an encrypted cache weren't worth the extra service. Plays from a copy still go through `/play/start` and heartbeats while online. |
| 17 | Grace with several rules, or across midnight? | A grace covers only the rules that granted it; a rule that expires later needs its own grace. At the midnight rollover a denied fresh start can still be graced, and its time is booked to the session's day (§3.3, §3.3a). |
| 18 | Which day does a play belong to? | The day its session started, online and offline. A file playing past midnight keeps that day's usage and limits; lockouts and blocks follow the clock (§3.2, §3.3). |
| 19 | Tablet clock while offline? | Anchored to the server's time at the last sync and carried forward with the since-boot clock. After a reboot without a sync, the tablet's clock is trusted (§4.3). |

| 20 | When is a request's transaction committed? | Before its response is sent (`Depends(get_conn, scope="function")`). With FastAPI's default the commit ran after the response, so a client's next request could miss the write. The nightly e2e test (§10.2) found it, and `test_db` guards it. |
| 21 | Do accidental taps use up a daily file? | No. Per-rule `count_after_seconds` (default 10): a file counts only after that much playing today, summed over the day (§3.1, §3.3). |
| 22 | May the day's story be listened to again? | Only within a listening budget: per-rule `replay_percent` (default 120 % of the file's length per day), then it is done for the day. Replaced the earlier "replays are free all day" (§3.3). |
| 23 | How does a new app reach the tablet? | Release images (since v0.20 the family image, §18) carry the signed APK of their tag, and the server imports it on start, so server and tablet stay on one release. The app installs it by itself on Android 12+ while nothing plays, without a confirmation; never with a system dialog in front of the child. Replaced "CI uploads the APK to the server" (§4.3). |
| 24 | Covers ffmpeg can't read? | Read from the tags with `mutagen` as a fallback (§6.4 row 1b), so a file macOS shows with a cover gets it here too. Files uploaded earlier pick it up via *Thumbnail → Automatic*. |
| 25 | Volume limit: per tablet or per child, and how? | Per tablet (`devices.max_volume_percent`), because the speakers differ. The app clamps the system media volume rather than scaling the player, so the slider shows the real level and the cap covers speaker and headphones (§4.3, §5.1). |

Decisions 26–29 for activities are in §13.8.

No open questions at the moment.

---

## 13. Activities (off-screen ideas)

Story-driven ideas for things to do away from the screen: draw, calculate, write. The tablet shows the current **story world** as a tile on Home. A world has chapters, and each chapter offers one task of each kind. The child picks one, does it on paper, and taps "I'm done!". Content is written by parents (or shipped as examples) and lives on the server. Prototype: *"Kids Media — Activities (proposal)"*.

### 13.1 Concepts

| Term | Meaning |
|---|---|
| **World** | A story with a guide character, e.g. *Lighthouse Island* with Mira and Pip. Has an age band (`6-7` now; `4-5`, `8-9` later), a colour, an optional cover, and 1–12 chapters. |
| **Chapter** | A story moment: `title`, `intro` (read before choosing), `outro` (shown when done). Offers 1–3 tasks, at most one per kind. |
| **Task** | `kind` (`draw` \| `calc` \| `write`), `minutes`, and per language `title`, `text` (the read-aloud prompt), `materials` (list of short words), `easier`, `harder` (parent hints, admin only). |
| **Activity days** | Per profile: the weekdays with activities (default Mon–Sat), plus one-off **days off** (date ranges with a note). |
| **Queue** | Per profile: the ordered list of worlds to run. |

All texts exist per language: `texts: {"de": {...}, "en": {...}, "el": {...}}`. The story is told in the language the parent picked for it in the queue (`world.lang`, §14.7), not in the app's language. On the tablet a missing language falls back to English, then German, then Greek. Text-to-speech uses the language of the text it reads.

### 13.2 Schedule

The story moves on **activity days only**. With `days` the weekday set, `days_off` the date ranges and `queue_start` the date counting starts from:

1. The activity days are the dates `≥ queue_start` whose weekday is in `days` and that aren't in a days-off range (inclusive).
2. Chapters are laid out over the activity days in queue order: chapter 1 of `queue[0]` on the first activity day, and so on; each world's chapters directly follow the previous world's. A world with *n* chapters takes *n* activity days, so with fewer activity days a week it runs into the next week.
3. The **current world** is the last queued world whose first chapter date is `≤ today`. Only the current world is shown on the tablet. If the first world starts later, nothing is shown yet.
4. A chapter is **unlocked** when its date is before today, or it is today and the local time is `≥ unlock_time` (default `07:00`). Missed chapters of the current world can be caught up; chapters of past worlds expire.
5. **Housekeeping** (on every read): when the current world isn't `queue[0]`, the worlds before it are removed and `queue_start` becomes the current world's first chapter date. Past worlds therefore never shift when days change later.
6. Changing `days` or `days_off` recomputes the dates of the current and later worlds from `queue_start`. Done tasks stay done.
7. Setting a queue whose first world differs from the current one starts it fresh: `queue_start = today`. Adding to an empty queue does the same. Reordering only later entries keeps `queue_start`.
8. No activity days at all → nothing scheduled, the tablet hides the tile.

Implemented as a pure function `schedule(...)` in `server/src/kidsmedia/activities.py` (no I/O), with table-driven tests and a frozen clock. Examples (they double as tests), with `queue_start` = Mon 2 Nov 2026 and a 6-chapter world first:

| Setup | Result |
|---|---|
| days Mon–Sat, no days off | Chapters on 2–7 Nov. Next world starts Mon 9 Nov. |
| days Mon, Tue, Thu, Sat, Sun | Chapters on Mon 2, Tue 3, Thu 5, Sat 7, Sun 8, Mon 9 Nov. |
| same, days off 7–8 Nov | Chapters on Mon 2, Tue 3, Thu 5, Mon 9, Tue 10, Thu 12 Nov. |
| Thu 5 Nov 06:59, days Mon–Sat | Chapters 1–3 unlocked; chapter 4 (Thu 5 Nov) unlocks at 07:00, `next_unlock_at` = 5 Nov 07:00. |
| Wed 4 Nov, days Mon, Tue, Thu | Today isn't an activity day; chapters 1–2 unlocked; next unlock Thu 5 Nov 07:00. |
| Tue 10 Nov, days Mon–Sat, 2 worlds | Current = world 2 (chapter 1 on Mon 9 Nov); housekeeping drops world 1, `queue_start` = 9 Nov. |
| `days` empty | Nothing scheduled, `world: null`. |

### 13.3 Device API

`GET /api/activities` (device token):
```json
{
  "server_time": "2026-11-05T14:20:00+01:00",
  "unlock_time": "07:00",
  "today_is_activity_day": true,
  "next_unlock_at": "2026-11-07T07:00:00+01:00",
  "world": {
    "id": 1, "slug": "lighthouse-island", "age_band": "6-7", "color": "#B84A12", "lang": "de",
    "cover": {"url": "/api/thumbs/w1-ab12cd.webp", "aspect": 1.0},
    "texts": {"de": {"name": "Leuchtturm-Insel", "guide": "mit Mira und der Möwe Pip"}, "en": {"name": "Lighthouse Island", "guide": "with Mira and Pip the seagull"}},
    "chapters": [{
      "position": 1, "date": "2026-11-02", "unlocked": true, "done_kinds": ["draw"],
      "image": {"url": "/api/thumbs/w1c1-9e4f20.webp", "aspect": 1.0},
      "texts": {"de": {"title": "…", "intro": "…", "outro": "…"}, "en": {"title": "…", "intro": "…", "outro": "…"}},
      "tasks": [{"kind": "draw", "minutes": 15,
                 "texts": {"de": {"title": "…", "text": "…", "materials": ["Papier", "Farbstifte"]}, "en": {…}}}]
    }]
  }
}
```
- `world` is `null` when nothing is scheduled. `cover` is `null` without a cover (the app draws the colour with a kind icon).
- All chapters of the current world are included, locked ones too (`date` may be `null` past the horizon), so the app can unlock offline at `date` + `unlock_time`.
- `easier` and `harder` are **not** sent to the tablet.
- `next_unlock_at`: when the next locked chapter of the current world unlocks, or `null`.

`POST /api/activities/done` `{world_id, position, kind}` → the same body as `GET`. Idempotent. 404 `{"reason":"unknown_task"}` if the world, chapter or kind doesn't exist; 409 `{"reason":"activity_locked"}` if the chapter isn't unlocked (or the world isn't current). The done row's `day` is the local date. One task finishes the chapter; the others stay open as extras. An optional `done_at` (the moment of the tap) is judged as in §14.4a.

The app caches the last `GET` (like the library) and queues `done` taps made offline, sending them on the next successful refresh. Activities don't use playback rules or minutes.

### 13.4 Admin API

- `GET /api/admin/activities` → `{settings: {days: [0,1,3,5,6], unlock_time: "07:00"}, days_off: [{id, from, to, note}], queue: [{world_id, first_date, last_date, status: "running"|"upcoming"}], worlds: [WorldSummary], log: [{date, world_id, position, kinds: ["draw"], done_at}], examples: [{slug, texts: {de: {name}, en: {name}}, imported: bool}]}`.
  - `WorldSummary`: `{id, slug, age_band, color, cover, texts, chapters, tasks, complete, warnings}`; `complete` = every chapter has all three kinds and every text block exists in all three languages; `warnings` = `[{code: "missing_translation"|"missing_kind", chapter, kind?, lang?}]`.
  - `log`: the done tasks of the last 14 days, newest first, one row per chapter and day.
- `PUT /api/admin/activities/settings` `{days, unlock_time}` (`days` ⊆ 0–6, `HH:MM`).
- `POST /api/admin/activities/days-off` `{from, to?, note?}` → the row; `DELETE /api/admin/activities/days-off/{id}`.
- `PUT /api/admin/activities/queue` `{world_ids, langs?}` (each at most once; `langs` = `{"<world id>": "de"|"en"|"el"}`, the story language per queued world, §14.7) → the same as `GET` (rule 13.2/7). Queue entries in `GET` carry `lang` (the language the tablet tells the story in) and `langs` (the languages the world has).
- `GET /api/admin/worlds/{id}` → the full world in pack format (§13.5) plus `id` and `cover`; easier/harder included.
- `PUT /api/admin/worlds/{id}` (pack JSON without `cover`) → saves texts and chapters; slug can't change.
- `PUT /api/admin/worlds/{id}/cover` (image body, as group covers) and `DELETE …/cover`.
- `PUT /api/admin/worlds/{id}/chapters/{position}/image` (image body) and `DELETE …/image` → `WorldFull`. Each chapter in `WorldFull` carries `image: {url, aspect} | null`.
- `DELETE /api/admin/worlds/{id}` → also removes it from queues and deletes its done rows and cover files.
- `PUT /api/admin/worlds/import?dry_run=true|false` (raw body: a zip with `world.json` and pictures, or the JSON alone; max 50 MB) → `{slug, world_id, replaced, chapters, tasks, complete, warnings}`; 422 `{"reason":"invalid_pack","params":{"errors":[…]}}` on invalid input. Importing an existing slug replaces its content and keeps its id (queue and done rows stay).
- `GET /api/admin/worlds/{id}/export` → a zip in pack format: `world.json` plus `cover.webp` and `chapter-<n>.webp` for the pictures it has, so export → import keeps the pictures.
- `POST /api/admin/worlds/examples/{slug}` → imports an example pack (a folder in `server/src/kidsmedia/packs/` or in `EXTRA_PACKS_DIR`, §13.5) like `import`, pictures included.

All are profile 1 in v1, but every table carries `profile_id` (§6.1).

### 13.5 Pack format (`world.json`)

```json
{
  "format": 1,
  "slug": "lighthouse-island",
  "age_band": "6-7",
  "color": "#B84A12",
  "cover": "cover.webp",
  "texts": {"de": {"name": "…", "guide": "…"}, "en": {"name": "…", "guide": "…"}},
  "chapters": [{
    "image": "chapter-1.webp",
    "texts": {"de": {"title": "…", "intro": "…", "outro": "…"}, "en": {…}},
    "tasks": [{"kind": "draw", "minutes": 15,
               "texts": {"de": {"title": "…", "text": "…", "materials": ["…"], "easier": "…", "harder": "…"}, "en": {…}}}]
  }]
}
```
Validation: `format` 1; `slug` `[a-z0-9-]{1,40}`; `age_band` one of `4-5`, `6-7`, `8-9`; `color` `#RRGGBB`; 1–12 chapters; 1–3 tasks per chapter with distinct kinds; `minutes` 1–60; languages only `de`/`en`/`el`, at least one per text block; `title` ≤ 80, `name`/`guide` ≤ 80, `intro`/`outro`/`text` ≤ 600, `easier`/`harder` ≤ 200, ≤ 6 materials of ≤ 30. `cover` and each chapter's optional `image` name a file inside the zip (or the pack folder), decoded like other images (§6.4, decompression-bomb limit), stored as `w<id>-<hash>` thumbnails. Zip entries are read by name only and never extracted to disk paths.

**Pictures.** A world has an optional cover and each chapter an optional picture, both square (1:1 recommended, any aspect accepted), stored as thumbnails (`w<id>-<hash>` for covers, `w<id>c<n>-<hash>` for chapters, sizes sm/md/lg as §6.4) and served through `/api/thumbs/…` with the device token. Without a chapter picture the tablet shows the world cover, without a cover the world colour with a kind icon.

**Example packs** are folders: `<packs>/<slug>/world.json` plus the pictures it names (`cover.webp`, `chapter-1.webp` …) and `art/*.svg`, the editable vector sources the pictures are rendered from (`scripts/render_pack_art.py`, 1024 × 1024 WebP). The sources are not read by the server. `<packs>` is `server/src/kidsmedia/packs/` (bundled: only `lighthouse-island` since v0.20) and `EXTRA_PACKS_DIR` (env, unset by default; the family image sets `/app/packs`, §18). A pack there wins over a bundled one with the same slug, and a missing folder is ignored. Every example pack must pass `python -m kidsmedia.packcheck <folder>`: valid and complete, a cover and a picture per chapter, problem sets (§15.2), simple materials (§15.5) and write texts (§15.4); the server's tests run it on the bundled packs, the family build on its own. And **the cover always shows the world's icon**: its one recognisable motif (the owl, the lighthouse, the rocket …), large and central enough to read on a small tile. A story that hides that motif (a mystery about a missing owl) still shows it on the cover; the chapter pictures tell the story.

### 13.6 Data

```
worlds(id, slug UNIQUE, age_band, color, cover_key NULL, cover_aspect NULL, content JSON, created_at, updated_at)
       -- content chapters carry image_key / image_aspect (server-side only) for chapter pictures
       -- content = the pack's texts + chapters (validated)
activity_settings(profile_id PK → profiles, days TEXT '012345', unlock_time TEXT '07:00',
                  queue JSON [world ids], queue_start DATE NULL,
                  langs JSON {world id: lang} DEFAULT '{}')   -- §14.7, story language per queued world
activity_days_off(id, profile_id → profiles, from_day DATE, to_day DATE, note, created_at)
activity_done(id, profile_id → profiles, world_id → worlds ON DELETE CASCADE, position, kind,
              day DATE, done_at, device_id → devices ON DELETE SET NULL)
              UNIQUE(profile_id, world_id, position, kind)
```

### 13.7 Screens

- **Tablet, Home:** the current world is the first tile (cover or colour, "THIS WEEK" label, world name, badge: "Chapter 3 is ready", "2 chapters ready", "1 chapter to catch up", "Done for today", "Next chapter on Thursday"). Hidden when `world` is null.
- **Tablet, World:** back, world name and guide, one button per chapter labelled with its weekday (done ✓, locked 🔒, "Next wk" past this week), the selected chapter's picture and story with **Listen**, and three big cards Draw / Calculate / Write (chip "Done" or "Extra").
- **Tablet, Task:** kind chip, chapter, title, the text in large type, **Listen**, materials and minutes chips, big "I'm done!".
- **Tablet, Done:** star, "Chapter N done!", the outro, "Back to the story".
- **Listen** uses Android `TextToSpeech` in the text's language, offline; no recordings.
- **Admin, Activities:** activity days (weekday toggles), days off (add/remove), story queue with computed dates and a language per story (reorder, add with a language, change the language, remove), worlds list (with "complete"/warnings), world editor (chapter tabs, de/en switch, chapter and task texts, save), import pack (dry run report, then import), example packs to add, and this week's log.

### 13.8 Decisions

| # | Question | Decision |
|---|---|---|
| 26 | Fixed weekly worlds or a queue? | A queue over configurable activity days (§13.2), because the child isn't home every day. |
| 27 | One task per chapter or a choice? | A choice of draw, calculate, write. One finishes the chapter; the others stay open as extras. |
| 28 | Read aloud how? | The tablet's `TextToSpeech`, so no recordings and it works offline. |
| 29 | Rewards, photos? | Not yet. `activity_done` is the hook for them later. |
| 30 | Greek? | A third language everywhere (decision 4): `el` catalogs in admin and app, `el` in packs, `rules.message_el`, `DEFAULT_LOCALE=el` allowed. |
| 31 | Order of the example worlds? | A fixed list in the server (`EXAMPLE_ORDER`), unknown slugs after it: the Greek mystery *The Owl Mystery* first, then Lighthouse Island, Rocket Kiwi, Forest Bakery, Coral City. |
| 32 | Pictures in packs? | A cover per world and a picture per chapter, as files next to `world.json` (zip or folder). Example packs keep SVG sources and rendered WebP files in the repo; the server only reads the WebP. |

---

## 14. Activities v2: levels, reading, read aloud, rewards

v0.14. Builds on §13; where this section and §13 differ, this section wins.

### 14.1 Levels per child

- `activity_settings.level`: `easier` | `normal` (default) | `harder`, per profile. `GET/PUT /api/admin/activities/settings` carry `level`.
- In a task's text block, `easier` and `harder` are now **full read-aloud texts** for that level (same register as `text`, ≤ 600 characters), no longer parent hints. A missing level text falls back to `text`.
- The tablet only receives the text for the child's level, in the field `text` (and `level` at the top of `GET /api/activities`). The admin sees and edits all three.

### 14.2 Reading (`kind: "read"`)

A fourth kind next to draw, calc and write. A chapter offers 1–4 tasks with distinct kinds; `complete` (§13.4) now means all four kinds, all three languages and both level texts plus questions for every level.

- **Text block of a `read` task** (per language): `title`, `text` / `easier` / `harder` = the reading text for each level (aim: normal 40–60 words, easier 20–30, harder 80–100; limit 900 characters), `materials` may be empty, and `questions` per level:
  ```json
  "questions": {"normal": [{"question": "…", "answers": ["…", "…", "…"], "correct": 1}],
                "easier": […], "harder": […]}
  ```
  1–2 questions per level, exactly 3 answers each (≤ 60 characters), `question` ≤ 140, `correct` 0–2. A missing level falls back to `normal`.
- **Device:** a `read` task carries `text` (the reading text for the level) and `questions` for the level, including `correct` (the tablet checks offline).
- **Tablet flow:** the Read card → the text in large type (short lines, generous spacing; **no Listen for the reading text**, Listen stays for the title and the questions) → "I've read it" → the questions, one at a time, three big answer buttons. Wrong: "Read it again — the answer is in the text" and back to the text. All right: done. `attempts` = the number of times the child went through the questions until all were right.
- `POST /api/activities/done` takes an optional `attempts` (≥ 1, read tasks only); `activity_done.attempts` stores it (NULL for other kinds). The admin log shows it.

### 14.3 Read aloud on the server

- **Engine:** Piper (`piper-tts`, runs on the CPU inside the container) plus `ffmpeg`. Every spoken text becomes an AAC file (`.m4a`, mono, 64 kbit/s) at `data/media/_speech/<key>.m4a`, with `key` = the first 20 hex characters of `sha256(voice id + "\n" + spoken text)`, so identical texts are shared and only changed texts are rendered again.
- **What is spoken:** world name; chapter title, intro, outro; task title and `text` (for every level); for `read` tasks the title and the questions with their answers, **not** the reading text.
- **Spoken form:** a text block may hold `say: {"<field>": "…"}` (e.g. `{"text": "…", "easier": "…"}`): the spoken version of that field. Used only for audio, never shown. ≤ 900 characters each.
- **Voices:** one per language. The server looks in `data/voices/` first (files the parent adds), then in the voices in the image (`/app/voices/`). `VOICE_DE`, `VOICE_EN`, `VOICE_EL` (env) choose a voice by name; default: the first present voice of the language in `voices.json`. Each voice's licence and attribution are recorded in `voices.json` (`server/src/kidsmedia/voices.json`; a parent may add entries in `data/voices/voices.json`) and shown in the admin; JOY: "JOY Greek voice — Gemma4GR project, CC BY-NC 4.0". Bundled: `de_DE-thorsten-medium`, `en_GB-cori-medium`, `el_GR-rapunzelina-low` from `rhasspy/piper-voices`, pinned to a commit: at image build the `.onnx` files are checked by sha256 (the build fails without one), the `.onnx.json` files are pinned by the commit in their URL. JOY is listed in `voices.json` first for Greek but with `"bundled": false` (v0.20): its licence is non-commercial and Hugging Face only hands it out after sign-in, so the public repository and image don't contain it. When its two files are present, in `data/voices/` or in `/app/voices/` (the family image copies them there, §18), JOY is the default Greek voice; otherwise `el_GR-rapunzelina-low` is. The admin shows how to add JOY by hand while Greek uses another voice.
- **Rendering:** a background worker thread in the app process renders what is missing after an import, an example add, a world save and at start-up (worlds in the queue first). Failures are logged and retried at the next start. Piper runs as a **separate process** (`python -m piper --model <id>.onnx --output-file <wav> -- <text>`, under `nice -n 10`, with `OMP_NUM_THREADS=1`), so the GPL-licensed Piper code never runs in the app's process and a render can't starve playback requests; it writes a WAV in a temporary folder (`.render-*`), ffmpeg turns it into the `.m4a` (temporary file, then rename). At start-up, before rendering, the worker deletes leftover temporary files and folders and, **only if every world loaded**, the `.m4a` files in `_speech/` that no world speaks any more (e.g. after an edit or a voice change). `SPEECH_WORKER=false` turns the worker off: nothing is queued or deleted, and the status shows `worker: false`, `pending: 0`.
- **Device:** each text block in `GET /api/activities` gets `audio: {"<field>": "/api/speech/<key>.m4a", …}` for the fields that are rendered; `questions` get `audio` per question: `{"question": url | null, "answers": [url | null, …]}`, or `null` when none of it is rendered. `GET /api/speech/{key}.m4a` (device token) answers with `X-Accel-Redirect: /_media/_speech/<key>.m4a`, so nginx needs no change. A field without audio uses the tablet's `TextToSpeech` as before.
- **Tablet:** Listen plays the audio file; the files of the current and next chapter are kept for offline use.
- **Admin:** `GET /api/admin/speech` → `{voices: [{lang, voice, source: "data"|"bundled", license, attribution}], rendered, pending, failed, worker}` (`voice: null` = no voice for the language; counts over every spoken text of every world; `worker` = the worker runs). `GET /api/admin/worlds/{id}` carries `audio: {field: "/api/admin/speech/<key>.m4a"}` in each text block (task blocks: `title`, and `text`/`easier`/`harder` except for read tasks) and, in task blocks, `question_audio: {level: [question audio | null, …]}` aligned with `questions`; both are output only and ignored by `PUT`. The editor shows a play button next to each spoken field (`GET /api/admin/speech/{key}.m4a`, admin session) and a field for the spoken form.

### 14.4 Rewards

- **Stars:** a task's first done row (per profile, world, chapter and kind) earns 1 star; a `read` task done with `attempts = 1` earns 1 more. The server records them as one `earn` row in `reward_ledger` (`delta` +1 or +2, `ref_id` = the `activity_done` id) when it stores the done row; a repeated done earns nothing. Stars are taken away only by "Open again" in the admin (§17, a `reopen` row): deleting a world deletes its done rows and stickers but keeps its `earn` rows. Balance = the sum of the ledger; earned = `earn` + `bonus` + `reopen` rows, spent = minus the `swap` and `wish` rows.
- **Atomic spending:** redeeming a swap, requesting a wish and approving or declining a request each run in one write transaction (`BEGIN IMMEDIATE`, §14.5): the balance and limit checks and the writes can't interleave with another request, so two taps at once can't spend the same stars twice.
- **Sticker album:** a chapter with at least one done task unlocks its picture as a sticker; one page per world, in queue/done order.
- **Swaps** (set per child in the admin): `{id, stars, rule_id, extra_items | extra_minutes, max_per_day, enabled}`, e.g. "5 stars → 1 more file today" for the audiobook rule, or "5 stars → 15 min" for the music rule. Redeeming adds a row to `adjustments` for today (the existing per-rule bonus, §3.5) and a ledger row. Lockouts and `block` still apply: a swap never opens a locked tablet. The swap's label on the tablet is built from the rule's groups and the amount.
  - A swap must be able to give something: saving (create or edit) one whose rule is in `block` mode, gives `extra_items` on a rule without `max_items`, or `extra_minutes` on a rule without `max_minutes` → 422 `swap_rule_unsuitable` with `{rule_id}`.
  - The tablet is offered (and may redeem, else 404 `unknown_swap`) only swaps whose rule is enabled, applies to the child, is **active today** (today's weekday in the rule's `days`) and still has the limit the swap raises (the rule may have been changed since).
- **Wishes** (set per child in the admin): `{id, texts: {de,en,el: {name}}, stars, enabled}`, e.g. "Choose dessert" for 10 stars. The child taps "I'd like this" → a pending request, only if the balance covers the wish's stars right now (else 409 `not_enough_stars` with `{stars, needed}`; nothing is deducted yet). The parent approves (stars deducted then; 409 `not_enough_stars` if the balance is too low by then, and the request stays pending) or declines in the admin. A request is decided once: deciding it again → 409 `request_decided` with `{status}` (a conditional update, so of two decisions at once only one counts). At most one pending request per wish, also enforced by a partial unique index (§14.5).
- **Device API:** `GET /api/rewards` → `{stars, album: [{world_id, texts, cover, stickers: [{position, image, earned_on}]}], swaps: [{id, stars, rule_id, extra_items, extra_minutes, used_today, max_per_day, groups: [names]}], wishes: [{id, texts, stars, request: null | {id, status, created_at}}]}`; `POST /api/rewards/swaps/{id}` → the new `GET` body (409 `not_enough_stars`, `swap_limit_reached`; 404 `unknown_swap` for a swap that isn't offered); `POST /api/rewards/wishes/{id}/request` → the new `GET` body (409 `request_pending`, `not_enough_stars`).
- **Admin API:** `GET /api/admin/rewards` → `{stars, earned, spent, swaps, wishes, requests: [{id, wish_id, status, created_at, decided_at}], ledger: [the last 50 spending rows (kind swap | wish), newest first]}`; CRUD `POST/PATCH/DELETE /api/admin/rewards/swaps[/{id}]` (422 `unknown_rule`, `swap_rule_unsuitable`) and `…/wishes[/{id}]`; `POST /api/admin/rewards/requests/{id}/approve|decline` (404 `unknown_request`, 409 `request_decided`, approve also 409 `not_enough_stars`).
- **Tablet:** a star counter on Home and on the world tile; a Rewards screen with the album, the swaps ("Swap 5 stars") and the wishes ("I'd like this", then "Waiting for a grown-up" / "Yes!" / "Not this time").
- **Admin:** a Rewards tab: balance, swaps editor (rule picker, amount, stars, max per day), wishes editor (name in de/en/el, stars), open requests with approve/decline, ledger.

### 14.4a Opening times for Ideas

- `activity_settings.windows`: a list of `{days: [0–6], from: "HH:MM", to: "HH:MM"}` (from < to, same day), per profile. Ideas are **open** when the current local time is inside any window whose `days` contain today's weekday. An empty list = always open (the default, as before).
- **Device:** `GET /api/activities` carries `windows`, `open_now` (bool) and `opens_at` (next opening as a local datetime, or null when open or never). The tablet re-evaluates the windows with its trusted clock, also offline.
- **Tablet:** outside the windows the world tile stays on Home, dimmed with a lock and the badge "Ideas open at 15:00" (or "… on Monday"); tapping it shows a friendly reason screen ("Ideas are resting until 15:00"). A task that is open when the window closes may still be finished ("I'm done!" within 15 minutes after closing is accepted).
- **Server:** `POST /api/activities/done` outside the windows (plus the 15 minutes) → 409 `activities_closed` with `{"opens_at": …}` (the next opening after now). Rewards (swaps, wishes) stay available: swapped time is still subject to all playback rules.
- **`done_at`:** `POST /api/activities/done` takes an optional `done_at`, the moment of the tap as an ISO datetime **with offset** (e.g. `2026-11-03T18:40:12+01:00`; without an offset → 422). The tablet sends it on every tap, from its trusted clock, also for taps it queued offline. The server judges the tap at that moment instead of now: the opening windows (plus the 15 minutes), the chapter's unlock and the current world (§13.2, from the queue as it is now: housekeeping runs as of now and isn't persisted for the past moment), and the done row's `day` and `done_at` are its local date and time. A `done_at` later than now (a tablet clock a little ahead) counts as now; one more than 7 days before now → 409 `activity_too_old` with `{"max_days": 7}`. Without `done_at` the tap counts as now (older apps). The tablet drops a queued tap on 404 and on any 409.
- **Admin:** `GET/PUT /api/admin/activities/settings` carry `windows`; the editor lists windows with weekday pills and from/to times.
- Examples (tests): windows `[{days: Mon–Fri, 15:00–18:30}, {days: Sat–Sun, 09:00–18:00}]` → Tue 07:30 closed, opens Tue 15:00; Tue 16:00 open; Fri 19:00 closed, opens Sat 09:00; Sun 18:10 closed, opens Mon 15:00; Tue 18:40 done for a task opened at 18:20 → accepted; Tue 18:50 → 409. A tap queued offline at Tue 18:40 (`done_at`) and sent at Tue 21:00 → accepted with day Tue; one at Tue 18:50 sent at 21:00 → 409 `activities_closed`; a tap at Sun 06:30 for Sunday's chapter (unlock 07:00) sent on Monday → 409 `activity_locked`; `done_at` 40 s ahead of the server → counts as now; `done_at` 7 days and 1 minute ago → 409 `activity_too_old`.

### 14.5 Data

```
activity_settings + level TEXT NOT NULL DEFAULT 'normal'          -- migration 0010
activity_settings + windows JSON NOT NULL DEFAULT '[]'             -- migration 0012
activity_done + attempts INTEGER NULL                            -- migration 0010
reward_swaps(id, profile_id → profiles, stars, rule_id → rules ON DELETE CASCADE,
             extra_items, extra_minutes, max_per_day, enabled, created_at)      -- migration 0011
reward_wishes(id, profile_id → profiles, texts JSON, stars, enabled, created_at)
wish_requests(id, profile_id, wish_id → reward_wishes ON DELETE CASCADE, status, created_at, decided_at)
wish_requests: UNIQUE (profile_id, wish_id) WHERE status = 'pending'          -- migration 0013
reward_ledger(id, profile_id, delta, kind 'earn'|'bonus'|'reopen'|'swap'|'wish', ref_id, created_at)
```
Migration 0013 adds the partial unique index (older duplicate pending requests: the oldest stays pending, the others become `declined`), backfills one `earn` row per existing `activity_done` row (+1, +2 for a read task with `attempts = 1`, `created_at` = `done_at`) and, for a profile whose ledger would then sum below zero (stars of a world deleted before 0013 were already spent), one `earn` row (`ref_id` NULL) that brings it to zero. `ref_id` has no FK, so ledger rows outlive their done rows, swaps and requests.

Spending (swap redeem, wish request, approve, decline) takes SQLite's write lock up front (`BEGIN IMMEDIATE`, or the transaction already holds it after an earlier write in the request), so its checks and writes are one atomic step; other writers wait (`busy_timeout`).
Speech files are content-addressed under `data/media/_speech/` and need no table; the worker keeps its state in memory.

### 14.6 Decisions

| # | Question | Decision |
|---|---|---|
| 33 | Levels? | Per child; full texts per level in the packs; the tablet gets only its level (§14.1). |
| 34 | Reading? | A fourth kind with a level-dependent text and 1–2 multiple-choice questions; Listen off for the text (§14.2). |
| 35 | Pronunciation? | Server-side Piper audio with a spoken form per field; JOY for Greek, added by the private family image (§18, was: kept in the private repository); tablet TTS only as fallback. No own recordings for now (§14.3). |
| 36 | Rewards? | Stars per done task (+1 for reading right first time), a sticker album, adjustable swaps into the existing per-rule bonus (e.g. 1 more audiobook), and wishes the parent approves (§14.4). |
| 37 | When can the child use Ideas? | Opening-time windows per weekday in the Ideas settings (§14.4a); outside them the tile is visible but locked. Rewards stay usable. |
| 38 | Story language vs. app language? | Separate (§14.7). The parent picks the language of each queued story in the admin; the child picks the app's language with flags on Home; the language setting behind the PIN was removed. |

### 14.7 Story language and the child's language flags

The story and the app's interface can be in different languages, e.g. a Greek story with a German interface.

- **Story language (admin).** Each world in the queue has a language, chosen when it is added and changeable in the queue row. It can be any language the world has its own texts in (`name`, `guide`); a world without German texts doesn't offer German. Stored in `activity_settings.langs` by world id; a world leaving the queue loses its entry, so adding it again asks again.
- **Default** when nothing is stored, and when the stored language is missing from the world (the world was edited): `DEFAULT_LOCALE` if the world has it, else English, German, Greek, the first the world has.
- **Device API.** `world.lang` in `GET /api/activities` (§13.3); the app's cached copy keeps it, so it holds offline. The tablet picks every story text (world, chapters, tasks, reading questions) in `world.lang`, then falls back as in §13.1; read aloud uses the language of the text it reads, so a Greek story is read in Greek. Stars, rewards, wishes and buttons ("I'm done!", "Listen") stay in the app's language. An app that doesn't know `lang` keeps using its own language.
- **Language flags (tablet).** Home shows one round button with the flag of the language in use (≥ 64 dp). A tap opens the three flags 🇬🇧 English, 🇩🇪 Deutsch, 🇬🇷 Ελληνικά (emoji flags, the chosen one ringed, each with its language name as content description); a tap on one switches the app's interface at once (the screen is rebuilt). v0.18: one button instead of three, so the header fits a tablet held upright. No parent PIN: the interface language is the child's to choose. Until a flag is tapped the app follows the system language. The parent settings have no language setting any more: the flags replaced it.

## 15. Activities v3: checks, photos, maths problems

v0.16. Builds on §13 and §14; where this section and those differ, this section wins. Goal: a task can't be finished by an accidental tap, and every kind has a real check.

### 15.1 How each kind is finished

| Kind | The check | Then |
|---|---|---|
| `draw` | The child takes a **photo** of the drawing on the tablet and confirms it ("Send this picture?" → Send / Take again). | Uploaded (§15.3), done. |
| `write` | Same: a photo of the written story. | Uploaded, done. |
| `calc` | A set of short story problems; the child types each result (§15.2). | Done when all are right. |
| `read` | The questions (§14.2), unchanged. | Done when all are right. |

There is no plain "I'm done!" button any more. Only where a task has no check (a `calc` task without problems, e.g. an older custom pack) the button stays, and it asks first: "Did you really finish?" → "Yes, done!" / "Not yet". Leaving a task (back) never finishes it.

### 15.2 Maths problem sets (`kind: "calc"`)

- **Text block of a `calc` task** (per language): `text` / `easier` / `harder` = a short read-aloud introduction for each level (the story moment and what to get ready, ≤ 600 characters as before), plus `problems` per level:
  ```json
  "problems": {"easier": [{"text": "…", "answer": 7, "hint": "…"}, …],
               "normal": […], "harder": […]}
  ```
  `text` ≤ 300 characters (one small story problem, in the world's story), `answer` an integer 0–999, `hint` ≤ 140 characters (non-empty: how to work it out with paper and pencil, never the answer). 1–6 problems per level; a missing level falls back to `normal`. Only `calc` tasks have `problems` (422 otherwise).
- **Example packs:** 3 problems for `easier`, 4 for `normal`, 5 for `harder`. Each set mixes kinds (adding, taking away, comparing, missing numbers, doubling and halving, patterns, simple two-step problems); `normal` and `harder` each include **at least one harder problem** (two steps, or a bigger number), so a set takes 10–15 minutes with paper and pencil. Number ranges for age band `6-7`: easier within 10, normal within 20, harder within 100. The problems and their answers are the same in every language (same order, same answers; the stories may differ in wording).
- `complete` (§13.4) also needs problems for all three levels in every language of every `calc` task, with the same number of problems and the same answers across languages.
- **Device:** a `calc` task carries `problems` for the child's level, including `answer` and `hint` (the tablet checks offline), and `audio` per problem: `{"text": url | null, "hint": url | null}` (§14.3: problems and hints are spoken and rendered like the other fields).
- **Tablet flow:** the Calc card → the introduction (Listen) with the materials → "Let's start" → the problems one at a time: the story (large type, Listen), "Problem 2 of 4", a big answer box and an on-screen number pad (0–9, delete, ✓; at most 3 digits, no system keyboard). Right: a short "Right!" and the next problem. Wrong: "Not quite — try again", the box clears; after the **second** wrong answer on a problem its hint appears (with Listen) and stays until it is solved. There is no skipping and no showing the answer. All right: done. `attempts` = 1 + the number of wrong answers in the set (so 1 = everything right at the first try), at most 50 (the server's limit). A queued done tap the server refuses with 422 is dropped, like 404 and 409. Leaving starts the set again next time.
- `POST /api/activities/done` stores `attempts` for `calc` tasks too (§14.2's rule now covers `read` and `calc`). **Stars:** 1 per done task as before; a `read` or `calc` task with `attempts = 1` earns 1 more.

### 15.3 Photos of drawings and stories

- **Tablet camera:** an in-app camera screen (CameraX: full-screen preview, back camera, one big round shutter button ≥ 96 dp, back button), not the system camera app (it would leave the app and the screen pinning). After the shot: the picture large, "Send this picture?" with **Send** and **Take again**. The camera permission is asked the first time; denied → a friendly "Ask a grown-up to allow the camera" screen with a button that asks again (and, if Android won't ask any more, opens the app's settings). Without a camera (none on the device) draw and write fall back to the confirm button of §15.1. A camera that fails to start is not "no camera": the screen says so and offers "Try again", never the confirm button.
- **Upload:** `POST /api/activities/photo?world_id=&position=&kind=&done_at=` (device token), body = the JPEG (`Content-Type: image/jpeg`, ≤ 15 MB; the app sends the long edge ≤ 2048 px, quality 85). Only `draw` and `write` (422 `photo_not_allowed` for other kinds). It is judged exactly like `done` (404 `unknown_task`, 409 `activities_closed` / `activity_locked` / `activity_too_old`, `done_at` for a photo taken offline), stores the photo, marks the task done (idempotent: a second photo of a done task is stored too, but earns nothing) and returns the `GET` body. 413 `photo_too_large`, 422 `bad_image` for a body that doesn't decode.
- `POST /api/activities/done` still accepts `draw` and `write` without a photo (older apps); the admin shows such rows as "no picture".
- **Offline:** the app keeps a photo it couldn't send as a file in its private storage together with the task and `done_at`, shows the task as done at once, and uploads it on the next successful refresh (oldest first; a photo is deleted locally once the server has stored it or refused it with a 4xx, like a queued done tap: its `done_at` is fixed, so a refusal would only repeat; a timeout or 429 keeps it).
- **Server:** the image is decoded with Pillow (decompression-bomb limit as §6.4), EXIF-rotated, stripped of all metadata (no location, no device data) and written as WebP: `lg` (long edge 1600 px) and `sm` (320 px) at `data/media/_photos/<key>-<size>.webp`, `key` = `p<id>-<sha256[:8]>`. Served only to the admin: `GET /api/admin/photos/{key}.webp?size=sm|lg` (admin session) → `X-Accel-Redirect: /_media/_photos/<key>-<size>.webp`, so nginx needs no change. The device can't read photos back (yet).
- **Bonus star:** the parent may give **one** bonus star per photo: `POST /api/admin/activities/photos/{id}/bonus` → a ledger row `kind = "bonus"`, `delta = +1`, `ref_id` = the photo id; again → 409 `bonus_given`. Counts as earned (§14.4).
- **Admin API:** `GET /api/admin/activities/photos?before_id=&limit=` (default 30, newest first) → `{photos: [{id, world_id, world_name, position, kind, task_title, taken_at, created_at, url_sm, url_lg, bonus: bool}], next_before_id}`; `DELETE /api/admin/activities/photos/{id}` (deletes the files and the row; the done row and its stars stay; a given bonus star stays).
- **Admin screen:** Ideas → **Pictures**: a grid of the photos, newest first (date, world, chapter, Draw/Write, the task title), tap → the large picture; "Give a bonus star ⭐" (then "Bonus star given"), "Delete" (asks first). (v0.19: replaced by Done activities, §17; the Activity log has no picture column.)
- **Data:**
  ```
  activity_photos(id, profile_id → profiles CASCADE, done_id → activity_done ON DELETE SET NULL,
                  world_id NULL (no FK, history), world_name TEXT, position, kind, task_title TEXT,
                  key, width, height, taken_at, created_at)
  ```
  `world_name` and `task_title` are snapshots (in the story language, §14.7), so photos outlive a deleted world. Photos are kept until the parent deletes them. Backups (§6.2) include them with `data/media`.

### 15.4 Writing tasks: small stories

A `write` task asks for a **small story** on paper, in the world's story, not single letters: `easier` 1–2 short sentences (with a starter the child can copy, e.g. "The owl is …"), `normal` 3–4 sentences, `harder` 5–6 sentences with a beginning, a middle and an end. The text says what the story is about and offers two or three helping words. The check is the photo (§15.3).

### 15.5 Materials

Activities only need simple things: **paper, a pencil, coloured pencils, scissors, glue**. Example packs use only these materials (a test checks every task's `materials` against the list per language: en *Paper, Pencil, Coloured pencils, Scissors, Glue*; de *Papier, Bleistift, Buntstifte, Schere, Kleber*; el *Χαρτί, Μολύβι, Ξυλομπογιές, Ψαλίδι, Κόλλα*), and their texts don't ask for anything else (no buttons, beads, coins, toys, paint). Scissors and glue appear only sometimes. Custom packs may use other materials; the admin warns ("not on the simple materials list").

### 15.6 Decisions

| # | Question | Decision |
|---|---|---|
| 39 | Accidental "I'm done!"? | Gone: every kind has a check (photo, problems, questions); only a task without a check keeps a button that asks first. |
| 40 | Stars for photos? | 1 star at once when the photo is sent; the parent can add 1 bonus star per photo in the admin. |
| 41 | Wrong maths answers? | Try again; the hint appears after the second wrong try; no skipping. |
| 42 | Maths problems per level? | 3 / 4 / 5 (easier / normal / harder), with at least one harder problem in normal and harder. |
| 43 | Materials? | Paper, pencil, coloured pencils, scissors, glue only (example packs). |

## 16. Gems 💎: unlock content by tokens

v0.17. A second currency next to the stars of §14.4, for unlocking media: a rule can lock its groups by **gems** instead of by time. Stars and gems are separate (a star swap never turns into gems, and gems never into stars).

### 16.1 The balance

- One balance per profile, a ledger: `gem_ledger(id, profile_id → profiles CASCADE, delta INT, kind 'admin'|'spend', rule_id → rules ON DELETE SET NULL, media_id → media ON DELETE SET NULL, day DATE NULL, title TEXT (snapshot of the file), note TEXT, created_at)`, unique `(profile_id, rule_id, media_id, day)` for `spend` rows. Balance = sum of `delta`; it can't go below 0 by an admin removal (422 `not_enough_gems`), and a spend never makes it negative (§16.2).
- **First step:** only the parent adds and removes gems in the admin (`kind = 'admin'`, with an optional note, e.g. "Room tidied"). **Later** the app may earn them (e.g. the morning routine finished in time) as a new `kind`; the ledger is ready for that.

### 16.2 Gem rules

- `gem_cost` on a rule (§3.1), with its scope, days, `count_after_seconds` and `replay_percent` as usual. A gem rule may also carry other limits (lockouts, minutes, items); every condition must hold (§3.2).
- **Starting** a file that doesn't count yet today for a gem rule needs `balance ≥` the sum of `gem_cost` of all active gem rules that cover it and don't count it yet. Otherwise `gems` with `{need, have}`.
- **Spending:** when the file **counts** for the rule (it has played `count_after_seconds` today, §3.3), the server books a `spend` row `−gem_cost` for that rule, file and day, once (unique key). Booked where `plays.seconds` grows: heartbeat and the offline batch (M7), before the evaluation of the same request. "Paid" means a spend row exists (a spend of 0 counts too). If the balance is lower than the cost by then (gems removed meanwhile), the heartbeat that reaches the count books nothing and denies (condition 5); the offline batch books what is left, down to 0.
- **One listen:** a counted file plays on and may be replayed today within the rule's `replay_percent` (§3.3 listening budget; `replay_percent` now applies with `max_items_per_day` **or** `gem_cost`). When the budget is used up, `item_limit` for the rest of the day; the next day the file costs gems again.
- `stream` doesn't check gems (like the item limit: ExoPlayer preloads). Grace never spends or skips gems: `gems` isn't graceable.
- `remaining_seconds` includes the listening budget left under every gem rule (as for item rules).
- **Priority** of reasons (§3.2): `blocked_today`, `lockout`, `item_limit`, `minute_limit`, `gems`.
- **Offline (M7):** the rules snapshot carries the gem rules, `usage.gem_balance` and `usage.gems_paid` (`{rule id: [media ids]}`, the files already paid today); the app's engine (`RulesEngine.kt`) applies condition 5 to the snapshot balance minus the gems it has seen spent offline, and the batch books the spends on the server.

### 16.3 API

- `GET /api/library` gains `gems: {balance, used: bool}` (`used` = an enabled gem rule exists, so the tablet shows the balance), and each group's `status` gains `gem_cost` (the gems a **new** file of the group needs today, null if none) — `items_left`, `minutes_left`, `unlocks_at` as before. Items gain `paid_today: bool` (a gem rule covers it and it is paid today for every gem rule that covers it; false without gem rules). An `item_limit` from a gem rule's used-up budget has no `limit` param.
- `POST /api/play/start` and `heartbeat`: the reason `gems` with `{need, have}`.
- Admin: `GET /api/admin/gems?limit=50` → `{balance, entries: [{id, delta, kind, title, note, created_at}]}` newest first; `POST /api/admin/gems {delta: -20…20 except 0, note?}` → the same (CSRF); a removal below 0 → 422 `not_enough_gems`. Rules API: `gem_cost` like the other limits; preview and "Right now" (§3.5) show gems.

### 16.4 Screens

- **Admin → Rules:** a **Gems 💎** card at the top: the balance large, buttons −1 / +1 / +5, a note field, the last entries ("+2 Room tidied", "−2 Episode 4: The Storm"). "Undo" on the newest admin entry (a counter entry, so the ledger stays complete). In the rule editor: "Costs gems: [n] 💎 per listen" (empty = none), summary "Each file from Cartoons costs 2 💎 per listen". Spends appear in the card's entries with the file.
- **Tablet:** while gems are `used`, Home shows the balance next to the stars (💎 3, also at 0). A tap on it opens the **gems screen**: the gems owned as pictures (a number above 15; "No gems yet" at 0), "Gems open" with every group whose new files cost gems (cover, name, 💎 cost; dimmed with a lock when there aren't enough; a tap opens the group), and "A grown-up gives you gems." In the player the balance is only shown. A group tile whose new files need gems shows "💎 2"; files show a small 💎 2 until paid today. Not enough: the tile is dimmed with a lock like other locks, and the reason screen shows the needed and the owned gems as pictures ("You need 2 💎 — you have 1"), no numbers only, so a non-reader gets it. When gems are spent at the count, a short "💎 −2" floats up from the balance.
- i18n in en, de, el.

### 16.5 Decisions

| # | Question | Decision |
|---|---|---|
| 44 | Token for unlocking? | Gems 💎, separate from the stars. |
| 45 | What do gems buy? | One listen: a file counted today, within `replay_percent`; tomorrow it costs again. |
| 46 | Scope? | The rule's groups, like the other rules; the cost is per file. |
| 47 | Who gives gems? | The parent in the admin first; later the app (e.g. morning routine). |

## 17. Done activities in the admin

v0.19. Replaces the **Pictures** section of §15.3 (the photo storage, the upload and the admin photo image endpoint stay).

### 17.1 What a chapter's state is (for reference)

A chapter never closes: it unlocks on its date (§13.2) and gets ✓ once **one** of its tasks is done; its other tasks stay "Extra" and can still be done, each earning its own star. A done task can be opened again by the child, but earns nothing new. Ideas close only by the opening times (§14.4a), and a world ends when the next one starts.

### 17.2 The list

Ideas → **Done activities**: every done task (`activity_done` row) of the profile, newest first, 30 per page, "Load more".
Each row: date and time, world, chapter, kind (Draw / Calculate / Write / Read), the task title (story language, §14.7), `attempts` for read and calc ("all right first time" / "3 tries"), the stars it earned (1, or 2 with the first-try extra, plus 1 with a bonus), and its **picture(s)** if any (small, tap → large, §15.3; "no picture" for draw/write without one).
Actions per row:
- **Give a bonus star ⭐** — once per done task, for every kind (was: per photo). Then "Bonus star given".
- **Open again** — asks first ("Open this task again? Its N stars are taken back and its picture is deleted."). The task is undone: the tablet shows it open again on its next refresh, and the child can do it again and earn its stars again.

### 17.3 API

- `GET /api/admin/activities/done?before_id=&limit=` (default 30, max 100) → `{entries: [{id, world_id, world_name, position, kind, task_title, done_at, attempts, stars, bonus, photos: [{id, url_sm, url_lg, taken_at}]}], next_before_id}`; `stars` = what this task earned and still holds (earn + bonus rows with its id).
- `POST /api/admin/activities/done/{id}/bonus` → the entry; ledger row `kind = "bonus"`, +1, `ref_id` = the done id; again → 409 `bonus_given`; unknown → 404 `unknown_done`.
- `DELETE /api/admin/activities/done/{id}` ("open again") → 204. In one transaction: deletes the done row and its photos (rows and files), and books a ledger row `kind = "reopen"`, `delta = −min(stars, balance)`, `ref_id` = the done id, so the star balance never goes below 0 (stars already swapped stay swapped). The task's sticker (§14.4) goes when its chapter has no done task left.
- Removed: `GET /api/admin/activities/photos` and `POST …/photos/{id}/bonus`, `DELETE …/photos/{id}` (photos go with their task). `GET /api/admin/photos/{key}.webp` stays.
- **Migration:** existing photo bonus rows (`ref_id` = photo id) get `ref_id` = that photo's done id (NULL when the photo or its done row is gone, so an old photo id is never read as a done id).

### 17.4 Decisions

| # | Question | Decision |
|---|---|---|
| 48 | Reset stars of Ideas? | Per done task in the admin: "Open again" undoes it and takes its stars back (never below 0); its picture goes too. |
| 49 | Bonus star? | Per done task, every kind (was per photo). |

## 18. Public code and the family image

v0.20. The code is public under the MIT licence; what can't or shouldn't be public is added by a private image.

- **`docdru/kidsmedia` (public):** server, admin, app, tests, the pack format and tools, one example pack (`lighthouse-island`). Its image `ghcr.io/docdru/kidsmedia` has the three freely licensed voices and no APK; anyone can run it and add JOY and packs by hand (§13.5, §14.3).
- **`docdru/kidsmedia-family` (private):** a Dockerfile `FROM ghcr.io/docdru/kidsmedia:<version>` that adds JOY to `/app/voices/`, the family's story packs to `/app/packs/` (`EXTRA_PACKS_DIR=/app/packs`) and the signed APK built from the public tag `v<version>` to `/app/apk-bundle/`. It runs `kidsmedia.packcheck` on its packs and checks JOY's sha256 before it pushes `ghcr.io/docdru/kidsmedia-family:<version>`. The keystore secrets live only there. Dependabot bumps the `FROM` version, so a public release becomes a pull request there.
- **The server runs the family image.** Its data, its tablets and the update path (§4.3) don't change: the APK is signed with the same key as before.

### 18.1 Decisions

| # | Question | Decision |
|---|---|---|
| 50 | Public or private? | Public code (MIT) for free CI and sharing; JOY (CC BY-NC 4.0) and the family's story packs stay private, in a family image built on the public one. |
| 51 | Which example pack is public? | `lighthouse-island`, the one the tests already use. The other four move to the family repository and keep their place in the example order (decision 31). |
| 52 | Where is the APK signed? | In the family repository, from the public tag; the public release has no APK. |
