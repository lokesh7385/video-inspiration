import os
import re
import socket
import threading
from typing import Any, Callable, Dict, Generator, List, Optional, Set
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv
import httplib2
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from database import get_connection, init_db, update_video_topic, upsert_video

load_dotenv()

# Enforce strict 10s socket timeout to eliminate hanging requests
socket.setdefaulttimeout(10.0)

TOPICS = [
    "Tech & Coding",
    "Growth, Trading & Hustles",
    "News",
    "Random Facts",
    "Entertainment",
    "Project",
]

TOPIC_KEYWORDS: Dict[str, List[str]] = {
    "Tech & Coding": [
        "machine learning", "neural network", "neural networks", "deep learning", "how to code", "data science",
        "python", "code", "coding", "programming", "ai", "ml", "neural", "algorithm", "algorithms",
        "linux", "rust", "javascript", "react", "api", "database", "git", "software", "developer",
        "tutorial", "engineering", "math", "mathematics", "llm", "agent", "agents",
        "computer", "frontend", "backend", "fullstack", "devops", "sql", "nosql", "cloud", "aws", "docker"
    ],
    "Growth, Trading & Hustles": [
        "price action", "market strategy", "side hustle", "side hustles", "passive income", "make money",
        "micro saas", "self improvement", "elon musk", "david goggins", "trading", "trader", "traders",
        "crypto", "forex", "stocks", "stock", "backtest", "backtesting", "chart", "charts", "indicator",
        "indicators", "ema", "vwap", "macd", "business", "startup", "startups", "entrepreneur",
        "entrepreneurship", "income", "saas", "agency", "freelancing", "freelancer", "growth", "mindset",
        "motivation", "discipline", "habits", "productivity", "focus", "goggins", "wealth", "finance",
        "investing", "investor", "quant"
    ],
    "News": [
        "geopolitics", "world news", "breaking news", "press conference", "foreign policy", "stock market",
        "news", "geopolitics", "economy", "economic", "market", "markets", "war", "election", "elections", "policy",
        "government", "world", "report", "analysis", "summit", "crisis", "india", "global",
        "inflation", "parliament", "congress", "president", "minister", "conflict", "treaty", "sanctions"
    ],
    "Random Facts": [
        "how it works", "why do", "did you know", "mind blowing", "science", "history", "space", "physics",
        "universe", "documentary", "explained", "fact", "facts", "mystery", "mysteries", "nature",
        "biology", "geography", "trivia", "why", "astronomy", "evolution", "cosmos", "ancient", "galaxy",
        "archaeology", "quantum"
    ],
    "Entertainment": [
        "music video", "official trailer", "official video", "live stream", "gameplay",
        "music", "song", "songs", "trailer", "movie", "comedy", "vlog", "gaming", "funny", "challenge",
        "f1", "formula 1", "sport", "sports", "stream", "anime", "podcast", "humor", "reaction",
        "concert", "acoustic", "dance", "entertainment"
    ],
    "Project": [
        "full stack project", "portfolio project", "build and deploy", "project tutorial", "step by step project",
        "from scratch", "end to end project", "clone app", "project build", "project demo", "project showcase",
        "project", "projects", "build", "building", "portfolio", "diy", "maker", "showcase", "clone"
    ]
}


def parse_duration_sec(duration_str: str) -> int:
    """Parses ISO 8601 duration strings (e.g., 'PT15M33S', 'PT1H2M10S', 'PT45S') into seconds."""
    if not duration_str:
        return 0
    match = re.match(r"^P(?:(\d+)D)?T(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$", duration_str)
    if not match:
        return 0
    days, hours, minutes, seconds = match.groups()
    return (
        int(days or 0) * 86400
        + int(hours or 0) * 3600
        + int(minutes or 0) * 60
        + int(seconds or 0)
    )


def auto_detect_topic(title: str, description: str = "", channel: str = "") -> str:
    """Uses domain and keyword scoring across title, description, and channel

    to classify a video into one of TOPICS.
    """
    title_l = (title or "").lower()
    channel_l = (channel or "").lower()
    desc_l = (description or "").lower()

    scores = {topic: 0.0 for topic in TOPICS}

    for topic, keywords in TOPIC_KEYWORDS.items():
        for kw in keywords:
            kw_l = kw.lower()
            if " " in kw_l:
                pattern = re.escape(kw_l)
            else:
                pattern = r"\b" + re.escape(kw_l) + r"\b"

            if re.search(pattern, title_l):
                scores[topic] += 3.0
            if re.search(pattern, channel_l):
                scores[topic] += 2.0
            if re.search(pattern, desc_l):
                scores[topic] += 1.0

    best_topic = max(scores, key=scores.get)
    if scores[best_topic] == 0:
        return "Entertainment"

    return best_topic


def parse_smart_inputs(raw_text: str) -> Dict[str, List[str]]:
    """Splits raw text by newlines, commas, or whitespace, ignores empty tokens (e.g. ',,'),

    and categorizes inputs into deduplicated lists of playlist IDs and single video IDs.
    """
    if not raw_text:
        return {"playlists": [], "videos": []}

    tokens = re.split(r"[,\s\n\r\t]+", raw_text)
    playlists: List[str] = []
    videos: List[str] = []

    for token in tokens:
        token = token.strip()
        if not token or token == ",":
            continue

        # 1. Check for list= parameter anywhere in the token
        if "list=" in token:
            m = re.search(r"[?&]list=([a-zA-Z0-9_-]+)", token)
            if m:
                pid = m.group(1)
                if pid and pid not in playlists:
                    playlists.append(pid)

        # 2. Check for watch?v= parameter
        if "watch?v=" in token:
            m = re.search(r"[?&]v=([a-zA-Z0-9_-]{11})", token)
            if m:
                vid = m.group(1)
                if vid and vid not in videos:
                    videos.append(vid)
            continue

        # 3. Check for youtu.be/ short links
        if "youtu.be/" in token:
            m = re.search(r"youtu\.be/([a-zA-Z0-9_-]{11})", token)
            if m:
                vid = m.group(1)
                if vid and vid not in videos:
                    videos.append(vid)
            continue

        # 4. Check for /embed/ or /shorts/
        if "/embed/" in token or "/shorts/" in token:
            m = re.search(r"/(?:embed|shorts)/([a-zA-Z0-9_-]{11})", token)
            if m:
                vid = m.group(1)
                if vid and vid not in videos:
                    videos.append(vid)
            continue

        # Clean token for raw ID detection
        clean_token = token.split("&")[0].split("?")[0].strip()
        clean_token = re.sub(r"^[^a-zA-Z0-9_-]+|[^a-zA-Z0-9_-]+$", "", clean_token)

        # 5. Raw playlist ID (starts with PL, UU, FL, RD, OLAK5uy_)
        if any(clean_token.startswith(pfx) for pfx in ("PL", "UU", "FL", "RD", "OLAK5uy_")) and len(clean_token) >= 12:
            if clean_token not in playlists:
                playlists.append(clean_token)
            continue

        # 6. Raw 11-char video ID
        if len(clean_token) == 11 and re.match(r"^[a-zA-Z0-9_-]{11}$", clean_token):
            if clean_token not in videos:
                videos.append(clean_token)
            continue

    return {"playlists": playlists, "videos": videos}


def extract_playlist_id(raw_str: str) -> Optional[str]:
    """Extracts a clean YouTube playlist ID from a single string or URL."""
    res = parse_smart_inputs(raw_str)
    if res["playlists"]:
        return res["playlists"][0]
    return None


class YouTubeClientPool:
    """Thread-safe manager for YouTube Data API v3 service instances with automatic key rotation."""

    def __init__(self, api_keys: Optional[List[str]] = None):
        if api_keys is None:
            raw_keys = os.getenv("YOUTUBE_API_KEYS", "")
            api_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
        self.api_keys = api_keys
        self.current_index = 0
        self._local = threading.local()
        self._lock = threading.Lock()

    def get_service(self):
        """Returns a thread-local YouTube client using current key and strict 10s timeout."""
        with self._lock:
            if not self.api_keys or self.api_keys == ["KEY_1", "KEY_2"]:
                raise ValueError(
                    "Invalid or placeholder YouTube API keys found in .env. Please configure real Google API keys."
                )
            current_key = self.api_keys[self.current_index]
            key_idx = self.current_index

        local_service = getattr(self._local, "service", None)
        local_key_idx = getattr(self._local, "key_idx", None)

        if local_service is None or local_key_idx != key_idx:
            http_client = httplib2.Http(timeout=10.0)
            local_service = build("youtube", "v3", developerKey=current_key, http=http_client)
            self._local.service = local_service
            self._local.key_idx = key_idx

        return local_service

    def rotate_key(self) -> None:
        """Rotates to the next available API key in the pool."""
        with self._lock:
            if not self.api_keys:
                return
            self.current_index = (self.current_index + 1) % len(self.api_keys)
            masked = (
                self.api_keys[self.current_index][:6] + "..."
                if len(self.api_keys[self.current_index]) > 6
                else "***"
            )
            print(f"[KeyRotation] Rotated to key index {self.current_index} ({masked})")

    def execute_with_retry(self, request_fn: Callable[[Any], Any]) -> Any:
        """Executes a YouTube API request with retry and key rotation on HTTP 403 or 429."""
        if not self.api_keys:
            raise ValueError("No YouTube API keys provided in .env.")

        attempts = 0
        total_keys = len(self.api_keys)
        last_error = None

        while attempts < total_keys:
            service = self.get_service()
            try:
                request = request_fn(service)
                return request.execute()
            except HttpError as err:
                status = getattr(err.resp, "status", None)
                if status is not None:
                    try:
                        status = int(status)
                    except (ValueError, TypeError):
                        pass

                if status in (403, 429):
                    print(f"[KeyRotation] HTTP {status} encountered with key index {self.current_index}. Rotating...")
                    self.rotate_key()
                    attempts += 1
                    last_error = err
                else:
                    raise
            except (socket.timeout, TimeoutError) as err:
                print(f"[KeyRotation] Socket timeout (10s) with key index {self.current_index}. Rotating...")
                self.rotate_key()
                attempts += 1
                last_error = err

        raise RuntimeError(
            f"All {total_keys} YouTube API keys exhausted, rate limited, or timed out. Last error: {last_error}"
        )


_default_pool = YouTubeClientPool()


def _extract_thumbnail(thumbnails: Dict[str, Any]) -> Optional[str]:
    """Returns the highest resolution thumbnail URL available."""
    for res in ("maxres", "standard", "high", "medium", "default"):
        if res in thumbnails and "url" in thumbnails[res]:
            return thumbnails[res]["url"]
    return None


def import_single_videos(
    video_ids: List[str],
    topic: str = "AUTO",
    default_label: int = 1,
    client_pool: Optional[YouTubeClientPool] = None,
) -> List[Dict[str, Any]]:
    """Batches up to 50 video_ids into a SINGLE youtube.videos().list call (1 quota unit)

    and upserts them into videos.db.
    """
    if not video_ids:
        return []

    init_db()
    pool = client_pool or _default_pool
    imported: List[Dict[str, Any]] = []
    use_auto_topic = topic.upper() == "AUTO"

    # Batch into chunks of 50
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i + 50]

        def build_req(service, b=batch):
            return service.videos().list(
                part="snippet,contentDetails,statistics",
                id=",".join(b),
            )

        try:
            resp = pool.execute_with_retry(build_req)
        except Exception as e:
            print(f"[ImportSingle] Error fetching batch of {len(batch)} videos: {e}")
            continue

        for item in resp.get("items", []):
            vid = item.get("id")
            snippet = item.get("snippet", {})
            content_details = item.get("contentDetails", {})
            statistics = item.get("statistics", {})

            title = snippet.get("title", "")
            channel = snippet.get("channelTitle", "")
            description = snippet.get("description", "")
            thumbnail = _extract_thumbnail(snippet.get("thumbnails", {}))
            views = int(statistics.get("viewCount", 0))
            duration_sec = parse_duration_sec(content_details.get("duration", ""))

            assigned_topic = auto_detect_topic(title, description, channel) if use_auto_topic else topic

            video_data = {
                "video_id": vid,
                "topic": assigned_topic,
                "title": title,
                "channel": channel,
                "description": description,
                "views": views,
                "duration_sec": duration_sec,
                "thumbnail": thumbnail,
                "label": default_label,
            }
            upsert_video(video_data)
            imported.append(video_data)

    print(f"[ImportSingle] Successfully imported {len(imported)} / {len(video_ids)} videos.")
    return imported


def enrich_videos_metadata(
    video_items: List[Dict[str, Any]],
    pool: Optional[YouTubeClientPool] = None,
) -> List[Dict[str, Any]]:
    """Batches 11-char video IDs into groups of up to 50 and makes a secondary call to
    youtube.videos().list(part="snippet,statistics,contentDetails", id="...")
    to populate real integer views and duration_sec in SQLite.
    """
    if not video_items:
        return []

    pool = pool or _default_pool
    # Map video_id to item dict
    id_map: Dict[str, Dict[str, Any]] = {v["video_id"]: v for v in video_items if v.get("video_id")}
    vids = list(id_map.keys())

    for i in range(0, len(vids), 50):
        batch = vids[i:i + 50]

        def build_req(service, b=batch):
            return service.videos().list(
                part="snippet,statistics,contentDetails",
                id=",".join(b),
            )

        try:
            resp = pool.execute_with_retry(build_req)
        except Exception as e:
            print(f"[EnrichMetadata] Error fetching view counts/details for batch of {len(batch)}: {e}")
            continue

        for item in resp.get("items", []):
            vid = item.get("id")
            if vid not in id_map:
                continue

            stats = item.get("statistics", {})
            content_details = item.get("contentDetails", {})
            snippet = item.get("snippet", {})

            # Populate real view count and duration
            views = int(stats.get("viewCount", 0))
            duration_sec = parse_duration_sec(content_details.get("duration", ""))

            target_video = id_map[vid]
            target_video["views"] = views
            target_video["duration_sec"] = duration_sec

            # Upgrade thumbnail if better resolution exists
            if snippet.get("thumbnails"):
                better_thumb = _extract_thumbnail(snippet.get("thumbnails", {}))
                if better_thumb:
                    target_video["thumbnail"] = better_thumb

            upsert_video(target_video)

    return video_items


def enrich_all_zero_view_videos(limit: int = 500) -> int:
    """Finds existing videos in videos.db with views=0 and enriches them in batches."""
    init_db()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT video_id, topic, title, channel, description, views, duration_sec, thumbnail, label FROM videos WHERE views = 0 OR views IS NULL LIMIT ?", (limit,))
        rows = cursor.fetchall()

    if not rows:
        return 0

    videos = [dict(r) for r in rows]
    enrich_videos_metadata(videos)
    return len(videos)


def _fetch_single_playlist(
    pid: str,
    topic: str = "AUTO",
    default_label: int = 1,
    pool: Optional[YouTubeClientPool] = None,
    max_pages: Optional[int] = 50,
) -> Dict[str, Any]:
    """Helper to fetch a single playlist, returning video list and status info."""
    pool = pool or _default_pool
    next_page_token: Optional[str] = None
    page_count = 0
    videos: List[Dict[str, Any]] = []
    error_msg = None
    use_auto_topic = topic.upper() == "AUTO"
    visited_tokens: Set[str] = set()

    while True:
        token = next_page_token
        if token in visited_tokens:
            break
        if token:
            visited_tokens.add(token)

        def build_req(service, current_pid=pid, current_token=token):
            return service.playlistItems().list(
                part="snippet",
                playlistId=current_pid,
                maxResults=50,
                pageToken=current_token,
            )

        try:
            response = pool.execute_with_retry(build_req)
        except Exception as err:
            error_msg = str(err)
            break

        items = response.get("items", [])
        if not items:
            break

        for item in items:
            snippet = item.get("snippet", {})
            resource_id = snippet.get("resourceId", {})
            video_id = resource_id.get("videoId")

            title = snippet.get("title", "")
            if not video_id or title in ("Private video", "Deleted video"):
                continue

            channel = snippet.get("videoOwnerChannelTitle") or snippet.get("channelTitle") or ""
            description = snippet.get("description", "")
            thumbnail = _extract_thumbnail(snippet.get("thumbnails", {}))

            assigned_topic = auto_detect_topic(title, description, channel) if use_auto_topic else topic

            video_data = {
                "video_id": video_id,
                "topic": assigned_topic,
                "title": title,
                "channel": channel,
                "description": description,
                "views": 0,
                "duration_sec": 0,
                "thumbnail": thumbnail,
                "label": default_label,
            }
            upsert_video(video_data)
            videos.append(video_data)

        page_count += 1
        next_page_token = response.get("nextPageToken")
        if not next_page_token or next_page_token in visited_tokens or (max_pages and page_count >= max_pages):
            break

    # Enrich all collected videos with real views and duration_sec in batches of 50
    if videos and not error_msg:
        try:
            enrich_videos_metadata(videos, pool)
        except Exception as e:
            print(f"[FetchPlaylist] Warning: Failed to enrich metadata for {pid}: {e}")

    return {
        "pid": pid,
        "count": len(videos),
        "error": error_msg,
        "videos": videos,
    }


def import_smart_inputs_stream(
    raw_text: str,
    topic: str = "AUTO",
    default_label: int = 1,
) -> Generator[Dict[str, Any], None, None]:
    """Universal parallel ingestion generator with real-time progress yielding for SSE streaming."""
    init_db()

    # Upfront check for placeholder or missing API keys in .env
    if not _default_pool.api_keys or _default_pool.api_keys == ["KEY_1", "KEY_2"]:
        yield {
            "step": 0,
            "total": 0,
            "pct": 100,
            "log": "❌ Error: Placeholder keys detected in .env (KEY_1, KEY_2). Please configure your Google YouTube Data API v3 key in .env.",
            "done": True,
            "total_added": 0,
            "breakdown": {t: 0 for t in TOPICS},
            "errors": [{"item": ".env", "error": "Placeholder keys KEY_1, KEY_2 detected in .env."}],
        }
        return

    parsed = parse_smart_inputs(raw_text)
    playlists = parsed["playlists"]
    video_ids = parsed["videos"]

    total_tasks = len(playlists) + (1 if video_ids else 0)
    if total_tasks == 0:
        yield {
            "step": 0,
            "total": 0,
            "pct": 100,
            "log": "No valid playlists or video links detected in input.",
            "done": True,
            "total_added": 0,
            "breakdown": {t: 0 for t in TOPICS},
            "errors": [],
        }
        return

    completed_steps = 0
    total_added = 0
    topic_breakdown: Dict[str, int] = {t: 0 for t in TOPICS}
    errors: List[Dict[str, str]] = []

    yield {
        "step": 0,
        "total": total_tasks,
        "pct": 5,
        "progress": 5,
        "log": f"Parsed {len(playlists)} playlist(s) and {len(video_ids)} individual video(s). Starting parallel ingestion...",
        "status": f"Parsed {len(playlists)} playlist(s) and {len(video_ids)} video(s). Starting ingestion...",
        "done": False,
    }

    # 1. Fetch single videos in 1 batch (super fast)
    if video_ids:
        yield {
            "step": completed_steps + 1,
            "total": total_tasks,
            "pct": int((completed_steps + 0.5) / total_tasks * 100),
            "progress": int((completed_steps + 0.5) / total_tasks * 100),
            "log": f"Importing {len(video_ids)} standalone video(s) with view counts via batch API...",
            "status": f"Importing {len(video_ids)} standalone video(s) with view counts...",
            "done": False,
        }
        try:
            v_added = import_single_videos(video_ids, topic=topic, default_label=default_label)
            total_added += len(v_added)
            for v in v_added:
                top = v.get("topic", "Entertainment")
                topic_breakdown[top] = topic_breakdown.get(top, 0) + 1
            completed_steps += 1
            yield {
                "step": completed_steps,
                "total": total_tasks,
                "pct": int(completed_steps / total_tasks * 100),
                "progress": int(completed_steps / total_tasks * 100),
                "log": f"✅ Successfully added {len(v_added)} standalone video(s) with view counts.",
                "status": f"Added {len(v_added)} standalone video(s) with view counts.",
                "done": False,
            }
        except Exception as e:
            completed_steps += 1
            errors.append({"item": "Standalone Videos", "error": str(e)})
            yield {
                "step": completed_steps,
                "total": total_tasks,
                "pct": int(completed_steps / total_tasks * 100),
                "progress": int(completed_steps / total_tasks * 100),
                "log": f"⚠️ Standalone video batch failed: {e}",
                "status": f"Standalone video batch error: {e}",
                "done": False,
            }

    # 2. Parallel thread pool for playlists (max_workers=4)
    if playlists:
        with ThreadPoolExecutor(max_workers=4) as executor:
            future_to_pid = {
                executor.submit(_fetch_single_playlist, pid, topic, default_label, _default_pool): pid
                for pid in playlists
            }

            for future in as_completed(future_to_pid):
                pid = future_to_pid[future]
                completed_steps += 1
                pct = int(completed_steps / total_tasks * 100)

                try:
                    res = future.result()
                    if res["error"]:
                        errors.append({"item": pid, "error": res["error"]})
                        yield {
                            "step": completed_steps,
                            "total": total_tasks,
                            "pct": pct,
                            "progress": pct,
                            "log": f"⚠️ Playlist '{pid}' failed (Private / 404 / Error): {res['error']}",
                            "status": f"Playlist '{pid}' failed: {res['error']}",
                            "done": False,
                        }
                    else:
                        cnt = res["count"]
                        total_added += cnt
                        for v in res["videos"]:
                            top = v.get("topic", "Entertainment")
                            topic_breakdown[top] = topic_breakdown.get(top, 0) + 1
                        yield {
                            "step": completed_steps,
                            "total": total_tasks,
                            "pct": pct,
                            "progress": pct,
                            "log": f"✅ Playlist '{pid}': imported {cnt} video(s) with view counts.",
                            "status": f"Fetched {completed_steps}/{len(playlists)} playlists... +{cnt} videos added with view counts",
                            "done": False,
                        }
                except Exception as exc:
                    errors.append({"item": pid, "error": str(exc)})
                    yield {
                        "step": completed_steps,
                        "total": total_tasks,
                        "pct": pct,
                        "progress": pct,
                        "log": f"❌ Playlist '{pid}' threw error: {exc}",
                        "status": f"Playlist '{pid}' error: {exc}",
                        "done": False,
                    }

    yield {
        "step": total_tasks,
        "total": total_tasks,
        "pct": 100,
        "progress": 100,
        "log": f"🎉 Ingestion finished! Added {total_added} total videos with view counts across {len(playlists)} playlist(s) and {len(video_ids)} video(s).",
        "status": f"Ingestion complete! Added {total_added} videos with view counts.",
        "done": True,
        "total_added": total_added,
        "breakdown": topic_breakdown,
        "errors": errors,
    }


def import_playlist(
    playlist_input: str,
    topic: str = "AUTO",
    default_label: int = 1,
    client_pool: Optional[YouTubeClientPool] = None,
    max_pages: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Synchronous import for backward compatibility with existing code."""
    parsed = parse_smart_inputs(playlist_input)
    imported: List[Dict[str, Any]] = []

    # Import videos
    if parsed["videos"]:
        imported.extend(import_single_videos(parsed["videos"], topic, default_label, client_pool))

    # Import playlists
    for pid in parsed["playlists"]:
        res = _fetch_single_playlist(pid, topic, default_label, client_pool, max_pages)
        imported.extend(res["videos"])

    return imported


def fetch_topic_candidates(
    query: str,
    topic: str,
    max_results: int = 25,
    video_duration: str = "medium",
    client_pool: Optional[YouTubeClientPool] = None,
) -> List[Dict[str, Any]]:
    """Searches YouTube candidate videos for a topic using search.list.
    
    Uses videoDuration ('medium' or 'long') to strictly filter out YouTube Shorts (<60s).
    Stores candidates with label=NULL (None) in videos.db.
    """
    if video_duration not in ("medium", "long"):
        raise ValueError("video_duration must be either 'medium' (4-20m) or 'long' (>20m) to block Shorts.")

    init_db()
    pool = client_pool or _default_pool
    candidates: List[Dict[str, Any]] = []

    def build_req(service):
        return service.search().list(
            part="snippet",
            q=query,
            type="video",
            videoDuration=video_duration,
            maxResults=min(max_results, 50),
        )

    response = pool.execute_with_retry(build_req)
    items = response.get("items", [])

    for item in items:
        video_id = item.get("id", {}).get("videoId")
        if not video_id:
            continue

        snippet = item.get("snippet", {})
        title = snippet.get("title", "")
        channel = snippet.get("channelTitle", "")
        description = snippet.get("description", "")
        thumbnail = _extract_thumbnail(snippet.get("thumbnails", {}))

        video_data = {
            "video_id": video_id,
            "topic": topic,
            "title": title,
            "channel": channel,
            "description": description,
            "views": 0,
            "duration_sec": 0,
            "thumbnail": thumbnail,
            "label": None,  # Candidate starts unrated
        }
        upsert_video(video_data)
        candidates.append(video_data)

    if candidates:
        try:
            enrich_videos_metadata(candidates, pool)
        except Exception as e:
            print(f"[Candidates] Warning: Failed to enrich candidate views/duration: {e}")

    print(
        f"[Candidates] Fetched and enriched {len(candidates)} candidates for query '{query}' ({video_duration}) under topic '{topic}'"
    )
    return candidates


def reclassify_existing_videos() -> Dict[str, Any]:
    """Re-runs auto_detect_topic on all existing videos in videos.db

    and updates any rows that belong to a new topic.
    """
    init_db()
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT video_id, topic, title, description, channel FROM videos")
        rows = cursor.fetchall()

    reclassified_count = 0
    topic_breakdown: Dict[str, int] = {t: 0 for t in TOPICS}

    for row in rows:
        vid = row["video_id"]
        current_topic = row["topic"]
        title = row["title"] or ""
        desc = row["description"] or ""
        channel = row["channel"] or ""

        detected_topic = auto_detect_topic(title, desc, channel)
        topic_breakdown[detected_topic] = topic_breakdown.get(detected_topic, 0) + 1

        if detected_topic != current_topic:
            update_video_topic(vid, detected_topic)
            reclassified_count += 1

    return {
        "total_videos": len(rows),
        "reclassified_count": reclassified_count,
        "topic_breakdown": topic_breakdown,
    }


if __name__ == "__main__":
    test_samples = [
        ("Building a Full-Stack Python and React Web Application", "Learn programming from scratch", "FreeCodeCamp"),
        ("Price Action Day Trading Strategy: 200 EMA and VWAP Setup", "How to backtest profitable forex and stock chart setups", "TradeMaster"),
        ("Global Economy Summit: Geopolitics and World Market Analysis", "International relations and crisis report", "Bloomberg News"),
        ("How the James Webb Telescope Discovered Ancient Galaxies in Deep Space", "Physics, universe, and space science documentary", "Cosmos Explained"),
        ("Top 10 Funniest GTA 5 Gaming Moments and Challenges Stream", "Hilarious gameplay and fails comedy", "GamerZone"),
    ]

    print("Testing auto_detect_topic routing across 5 topics:")
    for title, desc, chan in test_samples:
        detected = auto_detect_topic(title, desc, chan)
        print(f" -> '{title[:45]}...' => [{detected}]")
