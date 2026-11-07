# AI Event Photo Discovery Platform

The AI Event Photo Discovery Platform helps event attendees find their photographs without manually browsing a large gallery. Hosts create events and upload photographs, while attendees submit a selfie and receive photographs containing a matching face.

## Features

- JWT-based user registration and login
- Host event creation and event management
- Host-only photograph uploads
- Background photograph processing with Celery
- Face detection and embedding generation with InsightFace
- Vector similarity search with Qdrant
- Hierarchical candidate search for faster matching
- Selfie capture through the browser camera or image upload
- Matched-photo gallery and ZIP download
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
│   │   ├── routers/
│   │   ├── auth.py
│   │   ├── database.py
│   │   ├── hierarchy.py
│   │   ├── main.py
│   │   ├── models.py
│   │   └── tasks.py
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

Initialize the database tables:

```bash
python -c "from app.models import *; from app.database import create_db_and_tables; create_db_and_tables()"
```

Start the FastAPI server:

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
3. Upload event photographs from the event upload page.
4. Keep the Celery worker running while photographs are processed.
5. Monitor processing status and event analytics.

Only authenticated hosts can upload photographs.

### Attendee workflow

1. Register or log in as an attendee.
2. Open an event.
3. Capture a selfie or upload a selfie image.
4. The backend detects the face and searches the event's indexed face embeddings.
5. Open the matched-photo gallery and download selected photographs if required.

## Face Matching

Uploaded photographs are processed asynchronously. The worker detects faces using InsightFace, generates embeddings, stores face metadata in PostgreSQL, and indexes vectors in the event's Qdrant collection. Selfie searches use cosine similarity and deduplicate results by photograph. The current engineering threshold is `0.55`; similarity scores are not calibrated probabilities.

## Configuration and Security

The application currently contains development defaults for local use, including the local PostgreSQL connection, Redis broker, and JWT signing key. Before deployment:

- Set `DATABASE_URL` to a protected production database.
- Replace the JWT secret with a strong environment-provided secret.
- Restrict CORS origins to trusted frontend domains.
- Use protected database, Redis, Qdrant, and object-storage services.
- Configure HTTPS and secure cookie settings.
- Avoid committing `.env` files, uploaded photographs, model files, or credentials.

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
