# 🎬 Video Inspiration

> **A high-performance local YouTube curation, auto-classification, and recommendation engine.**

Video Inspiration helps you break free from algorithm rabbit holes by curating, organizing, and scoring YouTube videos from your favorite creators, playlists, and channels—stored locally in SQLite with zero telemetry.

---

## ✨ Features

- **🧠 Multi-Topic Auto-Categorization**: Automatically categorizes videos using intelligent keyword heuristics across 6 distinct topics:
  - `Tech & Coding` (Python, WebDev, AI, Systems, Architecture)
  - `Growth, Trading & Hustles` (Trading, Finance, SaaS, Mindset, Productivity)
  - `News` (Global events, Geopolitics, Economy, Tech reports)
  - `Random Facts` (Science, History, Curiosities, Deep dives)
  - `Entertainment` (Comedy, Sketches, Storytelling)
  - `Project` (Build logs, DIY, Case studies, Hands-on builds)
- **🚫 Anti-Fake-Guru & Anti-Clickbait Filter**: Automatic penalty scoring against scam triggers (`"get rich quick"`, `"easy money"`, `"$10k/day"`, `"guaranteed overnight"`) to elevate high-signal educational content.
- **⚡ 10x Parallel Streaming Ingestion**: Multi-threaded parallel workers fetch metadata, durations (ISO-8601 parsed), view counts, and channel details in real time.
- **📊 Live SSE Progress & Status Log**: Real-time Server-Sent Events (SSE) stream ingestion progress directly to an animated frontend progress bar with step-by-step logs.
- **🔗 Universal Smart Link Parser**: Paste any combination of URLs—single video links, full playlist links, raw video/playlist IDs, or channel handles.
- **🔄 Multi-Key API Quota Rotation**: Automatically fails over across multiple YouTube Data API v3 keys upon hitting rate limits (`quotaExceeded`).
- **🎯 Content-Based Recommendation**: Combines TF-IDF vector similarity, view count weightings, freshness decay, and user interest scoring.
- **🖥️ Desktop Launcher & 1-Click Startup**: Includes a desktop launcher (`.desktop`) and auto-bootstrapping launcher script (`./run.sh`).

---

## 🛠️ Tech Stack

- **Backend**: Python 3.10+, Flask, SQLite3
- **Data & ML**: `scikit-learn`, `pandas`, `numpy` (TF-IDF vectorization & scoring)
- **API**: Google YouTube Data API v3 (`google-api-python-client`)
- **Frontend**: Vanilla HTML5, CSS3 (Modern Glassmorphism & Responsive Grid), JavaScript (SSE, async fetch)

---

## 🚀 Quick Start

### 1. Clone the Repository
```bash
git clone https://github.com/lokesh7385/video-inspiration.git
cd video-inspiration
```

### 2. Configure YouTube API Keys
Copy the example environment file:
```bash
cp .env.example .env
```
Edit `.env` and add one or more YouTube Data API v3 keys (separated by commas for automatic rotation):
```env
YOUTUBE_API_KEYS=AIzaSyD_ExampleKey1,AIzaSyD_ExampleKey2
```

### 3. Launch the Application
Run the automated startup script:
```bash
chmod +x run.sh
./run.sh
```

The script will automatically:
1. Create a Python virtual environment (`.venv`) if not present.
2. Install all required dependencies from `requirements.txt`.
3. Initialize the SQLite database (`videos.db`).
4. Boot the Flask server at `http://localhost:5001`.
5. Open your default web browser automatically.

To stop the server at any time, press `Ctrl + C` in the terminal.

---

## 📥 Ingesting Videos & Playlists

1. Open the web interface at `http://localhost:5001`.
2. Click **"Ingest Videos"** or **"Add Links"**.
3. Paste any YouTube URLs:
   - Playlist: `https://www.youtube.com/playlist?list=PL...`
   - Single Video: `https://www.youtube.com/watch?v=...` or `https://youtu.be/...`
   - Mixed: Paste multiple links on separate lines.
4. Watch the real-time progress bar stream live updates as videos are fetched, categorized, and added to your local library.

---

## 📂 Project Structure

```text
├── app.py                      # Flask web server & SSE endpoints
├── database.py                 # SQLite database initialization & queries
├── ingest.py                   # Parallel YouTube API ingestion & URL parser
├── recommender.py              # TF-IDF similarity, freshness decay & scoring
├── requirements.txt            # Python package dependencies
├── run.sh                      # 1-click startup and auto-setup script
├── subscriptions.txt           # Starter channel list
├── video-inspiration.desktop   # Linux desktop application entry
├── templates/
│   └── index.html              # Responsive web dashboard
├── .env.example                # Sample environment configuration
└── .gitignore                  # Git ignore rules (protects .env & SQLite DB)
```

---

## 🔒 Security & Privacy

- **No Remote Telemetry**: Your video library and viewing preferences stay 100% on your local machine in `videos.db`.
- **Protected Secrets**: `.env` and SQLite database files are strictly ignored by `.gitignore` to prevent leaking API keys or personal data.

---

## 📄 License

MIT License. Feel free to use, modify, and distribute.
