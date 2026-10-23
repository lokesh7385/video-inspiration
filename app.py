import json
import os
import random
import re
from typing import Any, Dict, List
from flask import Flask, Response, jsonify, render_template, request, stream_with_context
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

from database import delete_demo_videos, get_videos_by_topic, update_label, update_video_topic
from ingest import fetch_topic_candidates, import_playlist, import_smart_inputs_stream
from recommender import TOPICS, score_topic_videos

app = Flask(__name__)
app.config["TEMPLATES_AUTO_RELOAD"] = True
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

YOUTUBE_FILLER_WORDS = {
    "video", "videos", "full", "new", "2024", "2025", "2026", "2027",
    "how", "why", "what", "but", "seconds", "course", "tutorial",
    "ep", "part", "vs", "versus", "top", "best", "chapter", "guide",
    "learn", "complete", "free", "session"
}

STOPWORDS = set(ENGLISH_STOP_WORDS).union(YOUTUBE_FILLER_WORDS)

TOPIC_DEFAULTS = {
    "Tech & Coding": "python programming",
    "Growth, Trading & Hustles": "trading price action strategy",
    "News": "geopolitics world economy",
    "Random Facts": "space physics history",
    "Entertainment": "acoustic music live session",
    "Project": "full stack project tutorial",
}

DEFAULT_SUFFIXES = ["deep dive", "project build", "explained", "lecture"]
GROWTH_SUFFIXES = ["case study", "strategy breakdown", "practical framework", "backtest"]
PROJECT_SUFFIXES = ["from scratch", "step by step", "full stack build", "case study", "architecture breakdown"]




@app.route("/")
def index():
    """Main dashboard displaying topic videos in either 'rate' or 'mytube' mode."""
    topic = request.args.get("topic", "Tech & Coding")
    if topic not in TOPICS:
        topic = "Tech & Coding"

    mode = request.args.get("mode", "rate").lower()
    if mode not in ("rate", "mytube"):
        mode = "rate"

    scored_videos, stats = score_topic_videos(topic)

    if mode == "mytube":
        # MyTube mode: Recommended videos with match_pct >= 50, sorted descending by match_pct
        filtered_videos = [v for v in scored_videos if v.get("match_pct", 0) >= 50]
        display_videos = sorted(
            filtered_videos,
            key=lambda x: (x.get("match_pct", 0), x.get("views", 0)),
            reverse=True,
        )
    else:
        # Rate mode: Unrated videos first, then rated videos (within each group, sorted by match_pct)
        unrated = [v for v in scored_videos if v.get("label") is None]
        rated = [v for v in scored_videos if v.get("label") is not None]

        unrated_sorted = sorted(
            unrated,
            key=lambda x: (x.get("match_pct", 0), x.get("views", 0)),
            reverse=True,
        )
        rated_sorted = sorted(
            rated,
            key=lambda x: (x.get("match_pct", 0), x.get("views", 0)),
            reverse=True,
        )
        display_videos = unrated_sorted + rated_sorted

    return render_template(
        "index.html",
        videos=display_videos,
        stats=stats,
        current_topic=topic,
        current_mode=mode,
        topics=TOPICS,
    )


@app.route("/api/rate", methods=["POST"])
def api_rate():
    """Accepts rating for a video (1 = Like, 0 = Dislike, None = Unrated) and recalculates scores."""
    data = request.get_json(silent=True) or {}
    video_id = data.get("video_id")
    label = data.get("label")  # 1, 0, or None
    topic = data.get("topic", "Tech & Coding")

    if not video_id:
        return jsonify({"status": "error", "message": "Missing 'video_id'"}), 400

    if label is not None:
        try:
            label = int(label)
            if label not in (0, 1):
                label = None
        except (ValueError, TypeError):
            label = None

    update_label(video_id, label)

    # Recalculate scores and stats for the topic
    scored_videos, stats = score_topic_videos(topic)
    scores_map = {v["video_id"]: v["match_pct"] for v in scored_videos}

    return jsonify({
        "status": "ok",
        "video_id": video_id,
        "label": label,
        "stats": stats,
        "scores": scores_map,
    })


@app.route("/api/change_topic", methods=["POST"])
def api_change_topic():
    """Reassigns a video to a different topic tab."""
    data = request.get_json(silent=True) or {}
    video_id = data.get("video_id")
    new_topic = data.get("new_topic")

    if not video_id or not new_topic:
        return jsonify({"status": "error", "message": "Missing 'video_id' or 'new_topic'"}), 400

    if new_topic not in TOPICS:
        return jsonify({"status": "error", "message": f"Invalid topic: '{new_topic}'. Must be one of {TOPICS}"}), 400

    update_video_topic(video_id, new_topic)

    return jsonify({
        "status": "ok",
        "video_id": video_id,
        "new_topic": new_topic,
        "message": f"Video successfully moved to {new_topic}",
    })


@app.route("/api/clear_demo", methods=["POST"])
def api_clear_demo():
    """Removes the 8 initial seeded demo videos from the database."""
    try:
        deleted_count = delete_demo_videos()
        return jsonify({
            "status": "ok",
            "deleted_count": deleted_count,
            "message": f"Successfully removed {deleted_count} demo videos from database.",
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
        }), 500


@app.route("/api/ingest", methods=["POST"])
def api_ingest():
    """Ingestion for candidate search or single/bulk playlist imports with AUTO topic routing."""
    data = request.get_json(silent=True) or {}
    topic = data.get("topic", "AUTO")
    playlist_id = data.get("playlist_id", "").strip()
    query = data.get("query", "").strip()

    if not playlist_id and not query:
        return jsonify({"status": "error", "message": "Please provide either a playlist_id or a search query."}), 400

    try:
        if playlist_id:
            imported = import_playlist(playlist_id, topic=topic, default_label=1)
            count = len(imported)

            # Compute breakdown of videos routed into each topic
            breakdown: Dict[str, int] = {}
            for v in imported:
                t = v.get("topic", "Entertainment")
                breakdown[t] = breakdown.get(t, 0) + 1

            return jsonify({
                "status": "ok",
                "count": count,
                "breakdown": breakdown,
                "message": f"Successfully imported {count} videos across topics.",
            })
        else:
            search_topic = "Tech & Coding" if topic.upper() == "AUTO" else topic
            candidates = fetch_topic_candidates(query, search_topic, max_results=20)
            count = len(candidates)
            return jsonify({
                "status": "ok",
                "count": count,
                "breakdown": {search_topic: count},
                "message": f"Successfully ingested {count} candidate videos for '{query}' under '{search_topic}'.",
            })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
        }), 500


@app.route("/api/ingest_stream", methods=["GET", "POST"])
def api_ingest_stream():
    """Server-Sent Events (SSE) streaming endpoint yielding real-time progress for single/bulk videos and playlists."""
    if request.method == "POST":
        data = request.get_json(silent=True) or {}
        raw_text = data.get("raw_text") or data.get("playlist_id") or data.get("input") or ""
        topic = data.get("topic", "AUTO")
    else:
        raw_text = request.args.get("raw_text") or request.args.get("playlist_id") or request.args.get("input") or ""
        topic = request.args.get("topic", "AUTO")

    raw_text = raw_text.strip()
    if not raw_text:
        def empty_stream():
            yield f"data: {json.dumps({'step': 0, 'total': 0, 'pct': 100, 'log': 'No input links or IDs provided.', 'done': True, 'total_added': 0, 'breakdown': {}, 'errors': []})}\n\n"
        return Response(stream_with_context(empty_stream()), mimetype="text/event-stream")

    def generate():
        try:
            for event in import_smart_inputs_stream(raw_text, topic=topic, default_label=1):
                yield f"data: {json.dumps(event)}\n\n"
        except Exception as e:
            err_event = {
                "step": 0,
                "total": 0,
                "pct": 100,
                "log": f"❌ Ingestion error: {str(e)}",
                "done": True,
                "total_added": 0,
                "breakdown": {},
                "errors": [{"item": "Stream", "error": str(e)}],
            }
            yield f"data: {json.dumps(err_event)}\n\n"

    response = Response(stream_with_context(generate()), mimetype="text/event-stream")
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["Connection"] = "keep-alive"
    return response


@app.route("/api/auto_discover", methods=["POST"])
def api_auto_discover():
    """Discovers fresh unrated candidate videos from YouTube search

    based on keywords from the user's liked videos in the topic.
    """
    data = request.get_json(silent=True) or {}
    topic = data.get("topic", "Tech & Coding")
    if topic not in TOPICS:
        topic = "Tech & Coding"

    videos = get_videos_by_topic(topic)
    liked_videos = [v for v in videos if v.get("label") == 1]

    # Extract keywords from liked video titles
    word_freq: Dict[str, int] = {}
    for v in liked_videos:
        words = re.findall(r"\b[a-zA-Z]{3,}\b", (v.get("title") or "").lower())
        for w in words:
            if w not in STOPWORDS:
                word_freq[w] = word_freq.get(w, 0) + 1

    if word_freq:
        # Sort words by frequency
        sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
        top_words = [w for w, _ in sorted_words[:2]]
        core_query = " ".join(top_words)
    else:
        core_query = TOPIC_DEFAULTS.get(topic, "tutorial")

    if topic == "Growth, Trading & Hustles":
        suffix = random.choice(GROWTH_SUFFIXES)
    elif topic == "Project":
        suffix = random.choice(PROJECT_SUFFIXES)
    else:
        suffix = random.choice(DEFAULT_SUFFIXES)

    search_query = f"{core_query} {suffix}"


    try:
        candidates = fetch_topic_candidates(search_query, topic, max_results=15)
        return jsonify({
            "status": "ok",
            "query": search_query,
            "count": len(candidates),
            "topic": topic,
            "message": f"Discovered {len(candidates)} fresh candidates for '{search_query}' under {topic}.",
        })
    except Exception as e:
        return jsonify({
            "status": "error",
            "message": str(e),
        }), 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
