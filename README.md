# AI Event Photo Discovery Platform

The AI Event Photo Discovery Platform helps event attendees find their photographs without manually browsing a large gallery. Hosts create events and upload photographs, while attendees pass a live selfie check and see only the photographs their face appears in.

## Features

- JWT-based user registration and login
- Host event creation and event management
- Host-only photograph uploads
- Background photograph processing with Celery
- Face detection and embedding generation with InsightFace
- Vector similarity search with Qdrant
- Hierarchical candidate search with a full-index recall pass
- Perceptual-hash duplicate detection for re-uploaded photographs
- Private photo access: hosts see every photo in their events; attendees see and download only the photos their live selfie matched
- Upload validation (images are checked by content, not file name) and generated thumbnails for fast galleries
- Role-aware dashboard; secret invite links and QR codes (`/join/{token}`) that the host can reset
- Host can delete an event, which erases its photos, face embeddings and attendee matches
- Offline search benchmark script (`backend/scripts/benchmark_search.py`)
- Selfie liveness check (random head-turn challenge plus a passive anti-spoofing model) so a photo taken from social media cannot be used to search
- Selfie capture through the browser camera with live guidance and a running progress percentage
- Matched-photo gallery and ZIP download of the matched photos only
- Processing status and event analytics
- PostgreSQL metadata storage
- Redis message broker for background tasks

## Technology Stack

### Frontend

- Next.js 15
- React 19
- TypeScript

### Backend

- FastAPI
- SQLModel
- PostgreSQL
- Celery
- Redis
- InsightFace and ONNX Runtime
- OpenCV and Pillow
- Qdrant
- JWT authentication

### Local Infrastructure

- Docker Compose
- PostgreSQL 16
- Qdrant
- Redis 7

## Project Structure

```text
.
├── backend/
│   ├── app/
│   │   ├── routers/        # auth, events, photos, users endpoints
│   │   ├── access.py       # who may see which event and photo
│   │   ├── auth.py         # password hashing and JWTs
│   │   ├── config.py       # settings from environment variables
│   │   ├── database.py
│   │   ├── hierarchy.py    # hierarchical face search
│   │   ├── liveness.py     # selfie liveness verification
│   │   ├── main.py
│   │   ├── models.py
│   │   └── tasks.py        # Celery photo processing
│   └── requirements.txt
├── frontend/
│   ├── src/
│   │   ├── app/
│   │   ├── components/
│   │   └── lib/
│   └── package.json
└── docker-compose.dev.yml
```

## Prerequisites

- Python 3.13 or a compatible Python 3 version
- Node.js and npm
- Docker Desktop
- Git

InsightFace downloads its model files the first time the face-analysis pipeline is initialized. The first photo-processing task can therefore take longer than subsequent tasks.

## Local Setup

### 1. Clone the repository

```bash
git clone https://github.com/AdithyaKashyapMs/AI-Event-Photo-Discovery-Platform.git
cd AI-Event-Photo-Discovery-Platform
```

### 2. Start PostgreSQL, Qdrant, and Redis

```bash
docker compose -f docker-compose.dev.yml up -d
```

The local services use these ports:

| Service | Address |
|---|---|
| PostgreSQL | `localhost:5432` |
| Qdrant | `localhost:6333` |
| Redis | `localhost:6379` |

The development database is named `eventphotos`. Do not use the development credentials in production.

### 3. Set up the backend

```bash
cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

The database tables are created automatically when the API starts. Start the FastAPI server:

```bash
python -m uvicorn app.main:app --reload
```

The backend runs at `http://localhost:8000`. API documentation is available at `http://localhost:8000/docs`.

### 4. Start the Celery worker

Open another terminal:

```bash
cd backend
source venv/bin/activate
celery -A app.tasks.celery_app worker --loglevel=info --pool=solo
```

The `solo` pool is the default local configuration because it avoids the prefork issue observed with Celery on macOS and Python 3.13. A production Linux deployment may use a different worker pool after validating it in that environment.

### 5. Set up and start the frontend

Open another terminal:

```bash
cd frontend
npm install
npm run dev
```

The frontend runs at `http://localhost:3000`.

To use a different backend URL, create `frontend/.env.local`:

```env
NEXT_PUBLIC_API_URL=http://localhost:8000
```

## Application Workflow

### Host workflow

1. Register or log in as a host.
2. Create an event.
3. Upload event photographs from the event upload page (JPEG, PNG, WebP, BMP or TIFF, up to 30 MB each).
4. Keep the Celery worker running while photographs are processed.
5. Share the invite link or QR code from the event page. The link contains a random invite code, so event IDs alone cannot be used to join; **Reset link** replaces it (attendees who already joined keep access).
6. Monitor processing status and event analytics, and browse every event photo from **All Photos**, where any photo can be deleted. Deleting a photo also removes its face embeddings and guests' matches to it; if a later re-upload of the same photo had been skipped as a duplicate, that copy is indexed instead.

Only the event's host can upload photographs, view all of them, or see analytics.

### Attendee workflow

1. Open the host's invite link or QR code, then register or sign in as an attendee to join the event.
2. Complete the live camera check: look straight and turn your head as instructed.
3. The backend verifies liveness, then searches the event's indexed face embeddings.
4. The matched photos are saved for that attendee and shown in **My photos**; they can be downloaded as a ZIP.

Attendees never see the whole event: they can only open photos their own live selfie matched at or above the match threshold. A new search replaces the previous matches.

## Face Matching

Uploaded photographs are processed asynchronously. The worker detects faces using InsightFace, generates embeddings, stores face metadata in PostgreSQL, and indexes vectors in the event's Qdrant collection. Selfie searches use cosine similarity and deduplicate results by photograph. Both thresholds are defined in `backend/app/hierarchy.py`:

- `CANDIDATE_SIMILARITY_FLOOR = 0.30`: the lowest similarity returned as a candidate. The gallery slider can go down to this value to inspect borderline results.
- `MATCH_SIMILARITY_THRESHOLD = 0.55`: the default similarity a candidate needs to be shown as a match. The gallery slider starts here.

Similarity scores are not calibrated probabilities. Face quality (Laplacian sharpness) is calculated and stored for analytics, but it is not used to rank matches, so a sharp photo cannot turn a weak identity match into an accepted one.

### Adaptive hierarchical index

`backend/app/hierarchy.py` builds an adaptive hierarchy over each event's faces after every upload batch:

- **Adaptive depth:** faces are clustered top-down with spherical k-means (4 branches per level). A group is split again only while it holds more than 32 faces, so dense parts of the face space get deeper trees and sparse parts stay shallow. On the sample event (340 faces) this gives 19 leaves of about 18 faces, 3 levels deep.
- **Adaptive branch selection:** a query is compared with every leaf centroid (cached in memory). It searches the closest leaves plus every leaf within a similarity margin of the best one, so an ambiguous face searches more of the index and a distinctive one searches less (capped at half the index).
- **Recall pass:** the app then merges a full-index search, so a face placed in an unselected leaf can never be missed.

Measured on the sample event (80 query faces, `backend/benchmarks/results/event2-after-adaptive-hierarchy.md`):

| Search | Median latency | True matches found (similarity ≥ 0.55) | Faces scanned |
|---|---|---|---|
| Exact search | 1.15 ms | 100% | 100% |
| Adaptive hierarchy pass alone | 2.04 ms | 100% | 16.9% |
| Hierarchy + recall pass (used by the app) | 3.34 ms | 100% | 100% |

The hierarchy cuts the faces scanned in the first pass to about 17% (4% on a 680-face index) without losing matches. At these event sizes Qdrant's own index is already fast, so the hierarchy is not a latency win; its value is pruning the search space, which matters as events grow. The earlier fixed-depth hierarchy (`event2-before-fixed-hierarchy.md`) produced near single-face branches and found only 79% of the nearest faces.

### Search benchmark

Search strategies are compared offline, never inside a user's request:

```bash
cd backend
source venv/bin/activate
python -m scripts.benchmark_search --event-id 1 --queries 50 --k 20
```

It samples indexed faces as queries and compares exact brute-force search, Qdrant's default HNSW search, the hierarchy pass alone, and the app's hierarchy-plus-recall search. For each it reports median and 95th-percentile latency and recall@k against exact search. Results are saved as Markdown and CSV in `backend/benchmarks/results/` for use in reports. Recall here means "returned the true nearest faces", not "found the same person"; identity accuracy needs a labelled dataset.

### Scale benchmark (10k to 300k faces)

Real events here have a few hundred faces, too few to show how search cost grows, so `scripts/benchmark_scale.py` generates synthetic events. They are calibrated against the real event-2 embeddings: unrelated faces have a mean similarity of about 0.05 and a 99th percentile around 0.33 to 0.39, and about a third of same-person pairs fall below the match threshold. Each query is a fresh "selfie" of a random guest, and recall is measured against exact search at the 0.55 match threshold.

```bash
cd backend
python -m scripts.benchmark_scale --sizes 10000 100000 300000 --queries 200 --qdrant-max 100000
```

Results from an 8 GB MacBook, 200 queries per size (p50 latency; full table in `benchmarks/results/scale-benchmark.md`):

| Faces | Exact scan | Hierarchy | Speed-up | Faces compared | Hierarchy match recall |
|---:|---:|---:|---:|---:|---:|
| 10,000 | 1.88 ms | 0.14 ms | 13.8x | 3.6% | 97.7% |
| 100,000 | 15.9 ms | 1.72 ms | 9.2x | 8.6% | 97.6% |
| 300,000 | 50.0 ms | 5.66 ms | 8.8x | 10.4% | 99.0% |

Inside Qdrant, 100,000 faces:

| Strategy | p50 | p95 | Match recall | Worst query |
|---|---:|---:|---:|---:|
| Exact search | 8.2 ms | 12.7 ms | 100% | 100% |
| HNSW (Qdrant default) | 4.0 ms | 6.3 ms | 94.4% | 0% |
| Hierarchy pass (filtered to selected leaves) | 6.5 ms | 11.2 ms | 97.6% | 0% |
| **Served by the app** (hierarchy + HNSW recall pass) | 11.6 ms | 15.3 ms | **99.7%** | **75%** |

What this shows:

- **The hierarchy removes about 90% of comparisons.** Exact search compares every face. The hierarchy compares only 4 to 10% of them, about 9 to 14 times faster, and keeps 97.6 to 99% of matches.
- **The hierarchy and HNSW miss different faces.** Inside Qdrant, HNSW (approximate graph search) misses 5.6% of matches at 100k faces. The hierarchy alone misses 2.4%. The app merges both results, so it finds 99.7%, and its worst query improves from 0% to 75%. That costs about 7.6 ms more than HNSW alone, which is small next to the face recognition step in the same request.
- **Tuning HNSW does not close the gap.** Raising Qdrant's search effort `hnsw_ef` on the same 100k faces barely helps, and some queries still find nothing:

  | `hnsw_ef` | 64 | 128 | 256 | 512 |
  |---|---:|---:|---:|---:|
  | Match recall | 93.2% | 93.2% | 93.7% | 95.6% |
  | Worst query | 0% | 0% | 0% | 0% |
  | p50 | 5.7 ms | 4.0 ms | 3.9 ms | 4.3 ms |

- **Inside Qdrant, the hierarchy is not a speed win.** Qdrant's filtered search over the selected leaves is slower than its own HNSW search. The hierarchy's job in the app is recall and pruning, not raw speed.
- **Limits:**
  - The data is synthetic.
  - 1M faces was not run, because it needs more than 8 GB RAM.
  - Only HNSW's search-time setting was tuned. Graph build settings (`m`, `ef_construct`) were not tested.
  - Accuracy on real faces needs a labelled evaluation set.

### Accuracy on real faces (labelling and evaluation)

The benchmarks above measure search, not whether the matched faces are really the same person. Measuring that needs ground truth: which photos each person is really in.

1. **Label.** As the host, open **Label Faces** in the event sidebar (`/dashboard/events/<id>/label`).
   - The app shows groups of faces that look alike. Untick anyone who is a different person, type a name and save.
   - It then shows that person's **harder faces**: side views, blur and small background faces that the grouping missed. Tick the ones that are really them.
   - This step matters. If the answers only contained faces the model already groups together, recall would look better than it really is.
   - Labels are stored in the `facelabel` table. Only the host can see them, and they never change what guests see. **Export labels (JSON)** downloads them for a report.
2. **Collect test selfies** (optional, but these give the numbers to report). Ask each labelled person for 1–6 selfies and get their permission. Save them as `backend/benchmarks/selfies/event<id>/<Name>/*.jpg`. The folder names must match the label names. This folder is gitignored.
3. **Evaluate:**

```bash
cd backend
python -m scripts.evaluate_matching --event-id 2                                      # leave-one-out (no selfies)
python -m scripts.evaluate_matching --event-id 2 --selfies benchmarks/selfies/event2  # real selfies
```

The report goes to `backend/benchmarks/results/eval-event<id>-<mode>.md`. It contains:

- **Recall and precision** at the app's 0.55 threshold.
- **Per-person results.**
- **A threshold sweep** from 0.35 to 0.75, for choosing the threshold with evidence.
- **Hard cases:** recall broken down by face sharpness and by face size.

A returned photo whose matching face is unlabelled counts as **unverified**, not as wrong, so finish labelling for an exact precision figure. Leave-one-out uses one of the person's own event faces as the query. That's easier than a real selfie, so treat its numbers as an upper bound.

Uploaded photographs whose perceptual hash matches an already processed photograph in the same event are marked `duplicate` and are not indexed again.

Photos uploaded before duplicate detection existed have no hash. To find and remove re-uploads among them, run `python -m scripts.backfill_duplicates --event-id <id>` for a dry run, then add `--apply`. It keeps the oldest copy and moves any matches and labels onto it.

## Selfie Liveness Check

A selfie search requires a live camera check, so someone cannot find another person's photos with a picture taken from social media.

1. The browser asks the backend for a challenge (`POST /events/{id}/liveness/challenge`). The backend picks a random sequence such as *look straight, turn left, look straight, turn right* and stores it in Redis. A challenge expires after 120 seconds, can be used only once, and is tied to the user and event.
2. The browser tracks the face with MediaPipe Face Landmarker (`frontend/src/lib/liveness.ts`), shows a running percentage below the camera, and captures one frame after each pose has been held for 0.6 seconds. This step only guides the user.
3. The frames are sent to `POST /events/{id}/match-selfie` with the `challenge_id`. The backend (`backend/app/liveness.py`) makes the decision:
   - each frame must contain exactly one clear face in the requested pose. The head turn is measured from the detector landmarks and cross-checked with InsightFace's 3D pose estimate;
   - every frame must show the same person (embedding similarity of at least 0.40 to the first frame);
   - InsightFace's passive liveness model must score each straight-on frame at least `LIVENESS_THRESHOLD` (default 0.8), which rejects printed photos and screens.
4. Only then is the face matched. All verified frames (straight-on and turned) are fused into one template, which matches more poses in event photos. The frames and their embeddings are processed in memory and discarded after the request.

Limitations: this is a challenge-response check plus a passive model, not a certified presentation-attack-detection system (ISO/IEC 30107-3). It stops static photos and screenshots. It cannot reliably stop a real-time deepfake, a realistic mask, or frames injected by a modified client.

Settings: `LIVENESS_THRESHOLD` (default `0.8`), `LIVENESS_REQUIRED` (default `true`; set to `false` only for local testing with uploaded images), and `REDIS_URL` (default `redis://localhost:6379/0`). The passive model (`liveness.onnx`, about 1.5 MB) is downloaded to `~/.insightface/addons/` the first time the backend runs a selfie search.

## Configuration and Security

All backend settings are environment variables with local development defaults (see `backend/app/config.py`):

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | `postgresql+psycopg://postgres:postgres@localhost:5432/eventphotos` | PostgreSQL connection |
| `APP_ENV` | `development` | `production` refuses to start without a strong `JWT_SECRET_KEY` and turns on secure cookies |
| `JWT_SECRET_KEY` | development key (warning logged) | Signs session tokens; at least 32 characters in production |
| `COOKIE_SECURE` | `true` in production, else `false` | Send the session cookie over HTTPS only |
| `COOKIE_DOMAIN` | unset | Parent domain (for example `.eventsnap.me`) when the API and frontend are sibling subdomains |
| `AUTO_MIGRATE` | `true` | Apply database migrations at API startup; with several API processes set `false` and run `alembic upgrade head` once per deploy |
| `RATE_LIMIT_ENABLED` | `true` | Per-IP, per-email and per-user request limits |
| `SELFIE_MAX_CONCURRENT` | `2` | Selfie searches that may run face recognition at the same time |
| `SELFIE_QUEUE_WAIT_SECONDS` | `20` | How long an extra selfie search waits for a free slot before returning `503` |
| `REDIS_URL` | `redis://localhost:6379/0` | Celery broker and liveness challenges |
| `QDRANT_URL` | `http://localhost:6333` | Vector database |
| `UPLOAD_DIR` | `backend/uploads` | Photo storage (thumbnails in `.thumbnails/`) |
| `FRONTEND_URL` | `http://localhost:3000` | Base URL in QR-code invite links |
| `CORS_ORIGINS` | `http://localhost:3000,http://127.0.0.1:3000` | Allowed browser origins (comma-separated) |
| `LIVENESS_REQUIRED` | `true` | Require the live selfie check |
| `LIVENESS_THRESHOLD` | `0.8` | Passive anti-spoofing score needed |
| `CELERY_WORKER_POOL` | `solo` | Celery worker pool |

Access rules are defined in `backend/app/access.py`: hosts can see and manage only their own events; attendees can see an event they joined, but only the photos their live selfie matched. Requests for anything else return `404` so IDs are not revealed.

What face data is stored, and who can delete it:

| Data | Stored? | Deleted by |
|---|---|---|
| Selfie camera frames and the selfie's face embedding | No, used in memory for one search | — |
| Face embeddings of every face in uploaded event photos (Qdrant, one collection per event), including attendees and bystanders who appear in them | Yes | The host: one photo at a time (`DELETE /photos/{id}`, **Delete** on a photo in the gallery) or the whole event (`DELETE /events/{id}`) |
| Event photos and thumbnails | Yes | The host, per photo or by deleting the event |
| Each attendee's matched photo IDs and similarity scores | Yes | The attendee (`DELETE /users/me/biometric-data`, **Delete my saved matches** on the dashboard), a new search (replaces them), or the host deleting the event |

The same summary is shown to attendees on the join and selfie pages.

### Sessions, CSRF and rate limits

- **Sessions:** login sets the session token in an `HttpOnly`, `SameSite=Lax` cookie, so page scripts (and any XSS bug) cannot read it. A second, secret-free cookie only tells the UI that a session exists. `POST /auth/logout` clears both. A `Bearer` token is still accepted for scripts and `/docs`.
- **CSRF:** cookie-authenticated `POST`/`PUT`/`DELETE` requests must come from an origin in `CORS_ORIGINS` (`403` otherwise). Login and registration are checked the same way.
- **Same site:** the browser only sends the cookie if the frontend and API are on the same site: `localhost` with `localhost`, or `eventsnap.me` with `api.eventsnap.me`. A frontend on `*.vercel.app` with the API elsewhere will not work; use a custom domain for both.
- **Login timing:** unknown emails take as long as wrong passwords, so login responses do not reveal which emails have accounts.
- **Rate limits** (`backend/app/ratelimit.py`, stored in Redis, `429` with `Retry-After`; if Redis is down they are skipped rather than locking users out):

| Endpoint | Limit |
|---|---|
| `POST /auth/login` | 10 per email and 30 per IP per 15 minutes |
| `POST /auth/register` | 10 per IP per hour |
| `GET/POST /invites/{token}…` | 30 per IP per minute |
| `POST /events/{id}/liveness/challenge` | 10 per user per minute |
| `POST /events/{id}/match-selfie` | 6 per user per minute, and at most `SELFIE_MAX_CONCURRENT` running at once |

Behind a reverse proxy, start uvicorn with `--proxy-headers --forwarded-allow-ips=<proxy IP>` so limits use the real client IP.

### Before deployment


- Set `DATABASE_URL` to a protected production database.
- Set `JWT_SECRET_KEY` to a strong secret (for example `python -c "import secrets; print(secrets.token_urlsafe(48))"`).
- Set `CORS_ORIGINS` and `FRONTEND_URL` to the real frontend domain.
- Use protected database, Redis, Qdrant, and object-storage services.
- Set `APP_ENV=production` and serve both the frontend and API over HTTPS.
- Avoid committing `.env` files, uploaded photographs, model files, or credentials.

## Database Migrations

The schema is managed with Alembic (`backend/migrations/`). The API applies pending migrations at startup. A database created before migrations existed is upgraded in place and stamped, so its data is kept.

```bash
cd backend
alembic upgrade head                                  # apply migrations
alembic revision --autogenerate -m "add something"    # after changing app/models.py; review the file it writes
alembic check                                         # fails if the models and migrations differ
```

`tests/test_migrations.py` checks that migrations build exactly the schema in `app/models.py`. It also checks that downgrading works and that an older database is upgraded in place without losing data.

## Data Consistency

Photos live in three places: PostgreSQL rows, Qdrant face embeddings, and files on disk. A crash in the middle of a delete, or data from older versions, can leave pieces behind. The cleanup script checks all three against the database:

```bash
cd backend
python -m scripts.cleanup_orphans            # dry run: report what would change
python -m scripts.cleanup_orphans --apply    # remove orphans
```

It removes:

- Qdrant collections of deleted events.
- Face embeddings whose photo is gone.
- Face records with no embedding.
- Thumbnails of deleted photos.

Files no photo points to are moved to `uploads/.orphaned/<time>/` so they can be recovered. Add `--purge-files` to delete them instead.

## Tests

The backend tests need no running services: they use SQLite, an in-memory Qdrant and a fake Redis. They cover access rules (host, matched attendee, unmatched attendee, outsider, rival host), invites, photo deletion, sessions and CSRF, rate limits, liveness challenges, the labelling tool, migrations, and orphan cleanup.

```bash
cd backend
source venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
```

## Useful Commands

Stop local infrastructure:

```bash
docker compose -f docker-compose.dev.yml down
```

Stop local infrastructure and remove development volumes:

```bash
docker compose -f docker-compose.dev.yml down -v
```

Build the frontend:

```bash
cd frontend
npm run build
```

## License

This project is intended for academic and demonstration purposes. Add the applicable license before distributing it as an open-source project.
