import os
import re
from typing import Any, Dict, List, Optional, Set, Tuple
import numpy as np
from sklearn.ensemble import RandomForestClassifier
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from database import get_connection, get_videos_by_topic, init_db, upsert_video

TOPICS = ["Tech & Coding", "Growth, Trading & Hustles", "News", "Random Facts", "Entertainment", "Project"]
SUBSCRIPTIONS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subscriptions.txt")


def load_subscriptions(path: str = SUBSCRIPTIONS_PATH) -> Set[str]:
    """Reads subscriptions.txt and returns a case-insensitive set of channel names."""
    if not os.path.exists(path):
        return set()
    with open(path, "r", encoding="utf-8") as f:
        return {line.strip().lower() for line in f if line.strip()}


def clickbait_penalty(title: Optional[str]) -> float:
    """Returns a penalty (0.0 to 0.25) if the title has excessive uppercase (>35% caps)
    or contains clickbait / fake-guru trigger phrases.
    """
    if not title:
        return 0.0

    penalty = 0.0

    # Check uppercase letter ratio
    letters = [c for c in title if c.isalpha()]
    if letters:
        caps_ratio = sum(1 for c in letters if c.isupper()) / len(letters)
        if caps_ratio > 0.35:
            penalty += 0.08

    title_lower = title.lower()

    # Standard clickbait triggers
    clickbait_triggers = ["shocking", "insane", "you won't believe", "prank"]
    if any(trigger in title_lower for trigger in clickbait_triggers):
        penalty += 0.10

    # Anti-Fake-Guru / Procrastination triggers
    fake_guru_triggers = [
        "get rich", "easy money", "without working", "lazy", "secret method",
        "10,000/day", "10000/day", "$10k/day", "10k/day", "overnight", "guaranteed"
    ]
    if any(trigger in title_lower for trigger in fake_guru_triggers):
        penalty += 0.20

    return min(penalty, 0.25)




def score_topic_videos(
    topic: str,
    subscriptions: Optional[Set[str]] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Scores all videos within a topic using Random Forest, Similarity Cold-Start,

    or Baseline engine, returning the scored video list and stats dictionary.
    """
    if subscriptions is None:
        subscriptions = load_subscriptions()

    videos = get_videos_by_topic(topic)
    if not videos:
        stats = {
            "num_rated": 0,
            "positives_count": 0,
            "negatives_count": 0,
            "engine_mode": "Baseline",
        }
        return [], stats

    # Partition rated videos
    positives = [v for v in videos if v.get("label") == 1]
    negatives = [v for v in videos if v.get("label") == 0]
    rated_videos = [v for v in videos if v.get("label") is not None]

    num_pos = len(positives)
    num_neg = len(negatives)
    num_rated = len(rated_videos)

    # Prepare text corpus (title + channel + description)
    text_corpus = [
        f"{v.get('title') or ''} {v.get('channel') or ''} {v.get('description') or ''}".strip() or "video"
        for v in videos
    ]

    vectorizer = TfidfVectorizer(
        stop_words="english",
        max_features=500,
        ngram_range=(1, 2),
    )

    try:
        tfidf_matrix = vectorizer.fit_transform(text_corpus)
    except ValueError:
        # Fallback if vocabulary could not be built
        tfidf_matrix = None

    base_scores = np.full(len(videos), 0.25, dtype=float)

    # Determine Engine Mode
    if num_pos >= 2 and num_neg >= 2 and tfidf_matrix is not None:
        engine_mode = "Random Forest"
        # Train Random Forest on rated videos
        rated_indices = [i for i, v in enumerate(videos) if v.get("label") is not None]
        X_train = tfidf_matrix[rated_indices]
        y_train = np.array([videos[i]["label"] for i in rated_indices], dtype=int)

        rf = RandomForestClassifier(
            n_estimators=100,
            random_state=42,
            class_weight="balanced",
        )
        rf.fit(X_train, y_train)

        # Probability of class 1
        classes = list(rf.classes_)
        if 1 in classes:
            pos_class_idx = classes.index(1)
            base_scores = rf.predict_proba(tfidf_matrix)[:, pos_class_idx]
        else:
            base_scores = np.full(len(videos), 0.25, dtype=float)

    elif num_pos >= 1 and tfidf_matrix is not None:
        engine_mode = "Similarity Cold-Start"
        # Mode B: Cosine similarity to mean vector of positives
        pos_indices = [i for i, v in enumerate(videos) if v.get("label") == 1]
        X_pos = tfidf_matrix[pos_indices]
        mean_pos_vector = np.asarray(X_pos.mean(axis=0))

        sims = cosine_similarity(tfidf_matrix, mean_pos_vector).flatten()
        base_scores = np.clip(sims, 0.0, 1.0)

    else:
        engine_mode = "Baseline"
        base_scores = np.full(len(videos), 0.25, dtype=float)

    # Post-Processing Rules
    scored_videos: List[Dict[str, Any]] = []
    for i, v in enumerate(videos):
        v_copy = dict(v)
        b_score = float(base_scores[i])

        channel_lower = (v.get("channel") or "").strip().lower()
        is_sub = channel_lower in subscriptions
        sub_boost = 0.20 if is_sub else 0.0

        penalty = clickbait_penalty(v.get("title"))
        final_score = b_score + sub_boost - penalty

        # Clamp final match percentage to integer [1, 99]
        match_pct = int(round(final_score * 100))
        match_pct = max(1, min(99, match_pct))

        v_copy["base_score"] = round(b_score, 4)
        v_copy["is_subscribed"] = is_sub
        v_copy["subscription_boost"] = sub_boost
        v_copy["clickbait_penalty"] = round(penalty, 4)
        v_copy["final_score"] = round(final_score, 4)
        v_copy["match_pct"] = match_pct

        scored_videos.append(v_copy)

    # Sort descending by match percentage, then views
    scored_videos.sort(key=lambda item: (item["match_pct"], item.get("views") or 0), reverse=True)

    stats = {
        "num_rated": num_rated,
        "positives_count": num_pos,
        "negatives_count": num_neg,
        "engine_mode": engine_mode,
    }
    return scored_videos, stats


def seed_demo_data() -> None:
    """Seeds 8 realistic test videos into videos.db across Tech & Coding and Entertainment,
    cleaning up any legacy placeholder IDs.
    """
    init_db()

    # Clean legacy demo placeholders
    with get_connection() as conn:
        conn.execute("DELETE FROM videos WHERE video_id LIKE 'tech_%' OR video_id LIKE 'ent_%'")
        conn.commit()

    demo_videos = [
        # Tech & Coding (2 positives, 2 negatives, 2 unrated)
        {
            "video_id": "7xTGNNLPyMI",
            "topic": "Tech & Coding",
            "title": "Python in 100 Seconds",
            "channel": "Fireship",
            "description": "Learn modern Python in 100 seconds and build production projects.",
            "views": 1250000,
            "duration_sec": 145,
            "thumbnail": "https://i.ytimg.com/vi/7xTGNNLPyMI/hqdefault.jpg",
            "label": 1,  # Positive
        },
        {
            "video_id": "aircAruvnKk",
            "topic": "Tech & Coding",
            "title": "But what is a neural network? | Deep learning chapter 1",
            "channel": "3Blue1Brown",
            "description": "A visual deep dive into neural networks, backpropagation, and calculus.",
            "views": 16800000,
            "duration_sec": 1152,
            "thumbnail": "https://i.ytimg.com/vi/aircAruvnKk/hqdefault.jpg",
            "label": 1,  # Positive
        },
        {
            "video_id": "r5Y1I1Zol2k",
            "topic": "Tech & Coding",
            "title": "Crypto Scam Exposed and Drama You Won't Believe",
            "channel": "TechBlogger",
            "description": "Exposing the biggest crypto scam drama in tech history.",
            "views": 120000,
            "duration_sec": 600,
            "thumbnail": "https://i.ytimg.com/vi/r5Y1I1Zol2k/hqdefault.jpg",
            "label": 0,  # Negative
        },
        {
            "video_id": "b3NxrZO8d9o",
            "topic": "Tech & Coding",
            "title": "SHOCKING TRUTH ABOUT CODING BOOTCAMPS IN 2026",
            "channel": "DevGuru",
            "description": "Is coding dead? You will never get a job after this bootcamp.",
            "views": 95000,
            "duration_sec": 580,
            "thumbnail": "https://i.ytimg.com/vi/b3NxrZO8d9o/hqdefault.jpg",
            "label": 0,  # Negative
        },
        {
            "video_id": "VyWAvY2CF9c",
            "topic": "Tech & Coding",
            "title": "Building Modern ML & Recommendation Systems Fast",
            "channel": "Fireship",
            "description": "Learn recommendation algorithms, TF-IDF vectorization, and Random Forest models.",
            "views": 420000,
            "duration_sec": 720,
            "thumbnail": "https://i.ytimg.com/vi/VyWAvY2CF9c/hqdefault.jpg",
            "label": None,  # Candidate Unrated
        },
        {
            "video_id": "zjkBMFhNj_g",
            "topic": "Tech & Coding",
            "title": "INSANE NEW JAVASCRIPT FRAMEWORK PRANK ON JUNIORS",
            "channel": "ClickBaitCoder",
            "description": "I tricked junior devs into learning a fake JS framework for 24 hours.",
            "views": 210000,
            "duration_sec": 480,
            "thumbnail": "https://i.ytimg.com/vi/zjkBMFhNj_g/hqdefault.jpg",
            "label": None,  # Candidate Unrated with Clickbait
        },
        # Entertainment (2 unrated cold-start videos)
        {
            "video_id": "kJQP7kiw5Fk",
            "topic": "Entertainment",
            "title": "Relaxing Acoustic Guitar Instrumental Session",
            "channel": "Peaceful Melodies",
            "description": "Calm acoustic music for study, concentration, and deep work.",
            "views": 150000,
            "duration_sec": 1800,
            "thumbnail": "https://i.ytimg.com/vi/kJQP7kiw5Fk/hqdefault.jpg",
            "label": None,  # Unrated
        },
        {
            "video_id": "9bZkp7q19f0",
            "topic": "Entertainment",
            "title": "YOU WON'T BELIEVE WHAT HAPPENED AT THE BACKSTAGE CONCERT",
            "channel": "GossipCentral",
            "description": "Shocking celebrity drama backstage caught on camera.",
            "views": 80000,
            "duration_sec": 420,
            "thumbnail": "https://i.ytimg.com/vi/9bZkp7q19f0/hqdefault.jpg",
            "label": None,  # Unrated with Clickbait
        },
    ]

    for video in demo_videos:
        upsert_video(video)


def print_score_table(topic: str, scored_videos: List[Dict[str, Any]], stats: Dict[str, Any]) -> None:
    """Prints a clean, formatted ASCII table of recommendation scores."""
    print("=" * 115)
    print(
        f"TOPIC: {topic:<18} | ENGINE MODE: {stats['engine_mode']:<22} | "
        f"RATED: {stats['num_rated']} (Likes: {stats['positives_count']}, Dislikes: {stats['negatives_count']})"
    )
    print("=" * 115)
    header = f"{'Match %':<9} | {'Label':<10} | {'Sub Boost':<10} | {'Penalty':<8} | {'Channel':<18} | {'Title'}"
    print(header)
    print("-" * 115)

    label_map = {1: "Like (1)", 0: "Dislike(0)", None: "Unrated"}

    for v in scored_videos:
        label_str = label_map.get(v.get("label"), "Unrated")
        sub_str = "+20%" if v.get("is_subscribed") else "None"
        pen_str = f"-{int(v.get('clickbait_penalty', 0) * 100)}%" if v.get("clickbait_penalty") else "0%"
        title_trunc = v.get("title", "")
        if len(title_trunc) > 50:
            title_trunc = title_trunc[:47] + "..."

        row = (
            f"{str(v['match_pct']) + '%':<9} | "
            f"{label_str:<10} | "
            f"{sub_str:<10} | "
            f"{pen_str:<8} | "
            f"{v.get('channel', ''):<18} | "
            f"{title_trunc}"
        )
        print(row)
    print()


if __name__ == "__main__":
    print("Seeding demo test videos into videos.db...")
    seed_demo_data()

    # Score Tech & Coding
    tech_videos, tech_stats = score_topic_videos("Tech & Coding")
    print_score_table("Tech & Coding", tech_videos, tech_stats)

    # Score Entertainment
    ent_videos, ent_stats = score_topic_videos("Entertainment")
    print_score_table("Entertainment", ent_videos, ent_stats)
