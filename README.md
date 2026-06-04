# Genset Call Analyzer

Upload a Marathi (or other Indian language) sales call recording → Get an automatic transcript and structured ERP-ready summary.

**Stack:** Vite + React frontend, FastAPI backend, Sarvam AI (transcription), Claude (summarization)

```
┌─────────────┐     ┌──────────────┐     ┌────────────┐     ┌───────────┐
│ Upload Audio │────▸│  Sarvam AI   │────▸│  Claude AI  │────▸│ ERP-Ready │
│   (.aac/.mp3)│     │  (Saarika)   │     │  (Sonnet)   │     │  Summary  │
└─────────────┘     └──────────────┘     └────────────┘     └───────────┘
```

---

## Quick Start (5 minutes)

### 1. Get your API keys

| Service | Sign up | Free tier |
|---------|---------|-----------|
| **Sarvam AI** | https://dashboard.sarvam.ai | Free credits on signup |
| **Anthropic** | https://console.anthropic.com | $5 free credits |

### 2. Setup backend

```bash
cd backend

# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate        # Mac/Linux
# venv\Scripts\activate         # Windows

# Install dependencies
pip install -r requirements.txt

# Configure API keys
cp .env.example .env
# Now open .env and paste your actual API keys
```

### 3. Setup frontend

```bash
cd frontend

npm install
```

### 4. Run

Open **two terminal windows:**

**Terminal 1 — Backend:**
```bash
cd backend
source venv/bin/activate
python main.py
```
Backend starts at http://localhost:8006

(See [docs/federation-cookbook.md](docs/federation-cookbook.md) for how the frontend mounts inside the KALA shell. Standalone `npm run dev` still works for solo dev.)

**Terminal 2 — Frontend:**
```bash
cd frontend
npm run dev
```
Frontend starts at http://localhost:5178

**Terminal 3 — Ngrok** (required for Plivo webhooks to reach your local backend):
```bash
ngrok http --domain=ocelot-boggle-retool.ngrok-free.dev 8006
```
Exposes your backend at `https://ocelot-boggle-retool.ngrok-free.dev` — the URL Plivo's Application is configured to call. Keep this terminal open while testing phone calls; closing it stops the tunnel and Plivo calls will fail.

If you don't have the reserved domain, run `ngrok http 8006` instead and update `SERVER_BASE_URL` in `backend/.env` + the Plivo Application's Answer URL each time (the random URL changes per restart).
 
### 5. Use it

1. Open http://localhost:5178
2. Check the status bar — all three dots should be green
3. Upload a call recording (AAC, MP3, WAV, M4A)
4. Select the call language (default: Marathi)
5. Click **"Analyze Call Recording"**
6. View the transcript, structured summary, and raw JSON

---

## Project Structure

```
genset-call-analyzer/
├── backend/
│   ├── .env.example        ← Copy to .env and add your API keys
│   ├── requirements.txt
│   └── main.py             ← FastAPI server (Sarvam + Claude pipeline)
│
├── frontend/
│   ├── package.json
│   ├── vite.config.js      ← Dev proxy to backend
│   ├── index.html
│   └── src/
│       ├── main.jsx
│       ├── index.css
│       └── App.jsx          ← Main UI component
│
└── README.md
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/health` | Check backend + API key status |
| POST | `/api/analyze` | Upload audio → transcript + summary |
| POST | `/api/summarize` | Paste transcript → summary only |

### POST /api/analyze
```
Content-Type: multipart/form-data

audio: <file>
language: "mr-IN"   (optional, default: mr-IN)
```

### POST /api/summarize
```json
{
    "transcript": "call transcript text here",
    "agent_name": "optional",
    "caller_number": "optional"
}
```

---

## Supported Languages

| Code | Language |
|------|----------|
| `mr-IN` | Marathi (default) |
| `hi-IN` | Hindi |
| `en-IN` | English (Indian) |
| `gu-IN` | Gujarati |
| `ta-IN` | Tamil |
| `te-IN` | Telugu |
| `kn-IN` | Kannada |
| `bn-IN` | Bengali |

---

## Extracted Fields

The AI extracts these fields from every call:

- **Customer details** — name, company, contact, location
- **Enquiry type** — new purchase, rental, service, spare parts
- **Genset specs** — kVA, fuel type, brand, phase, usage type
- **Deal info** — budget, timeline, competitor mentions
- **Requirements** — silent type, AMF panel, etc.
- **Next steps** — site visit, quotation, callback, demo
- **Lead scoring** — sentiment, hot/cold lead flag

---

## Next Steps (Stage 2)

Once you've validated the quality:

1. **Cloud telephony integration** — Add Exotel/Ozonetel webhooks so calls are processed automatically
2. **ERP push** — Auto-create leads in your ERP from the structured JSON
3. **Dashboard** — Daily call analytics, lead pipeline view
4. **Batch processing** — Process a day's worth of recordings in one go

---

## Cost Estimate

For 100-150 calls/day (~5 min average):

| Service | Estimated Cost |
|---------|---------------|
| Sarvam AI (transcription) | ₹250-750/day |
| Claude (summarization) | ₹125-190/day |
| **Total** | **₹400-950/day (~₹15,000-28,000/month)** |
