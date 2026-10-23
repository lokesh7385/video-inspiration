import os
import sqlite3
from typing import Any, Dict, List, Optional

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "videos.db")


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    """Returns a SQLite connection with Row factory enabled, 30s timeout, and WAL mode."""
    conn = sqlite3.connect(db_path, timeout=30.0)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(db_path: str = DB_PATH) -> None:
    """Initializes the SQLite database with the videos table schema."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS videos (
                video_id TEXT PRIMARY KEY,
                topic TEXT NOT NULL,
                title TEXT,
                channel TEXT,
                description TEXT,
                views INTEGER DEFAULT 0,
                duration_sec INTEGER DEFAULT 0,
                thumbnail TEXT,
                label INTEGER DEFAULT NULL
            )
            """
        )
        conn.commit()


def upsert_video(video_dict: Dict[str, Any], db_path: str = DB_PATH) -> None:
    """Inserts a new video or updates an existing record.
    
    If the video already exists, existing labels are preserved unless a new
    non-null label is provided in video_dict.
    """
    if "video_id" not in video_dict or "topic" not in video_dict:
        raise ValueError("video_dict must contain 'video_id' and 'topic'")

    params = {
        "video_id": str(video_dict["video_id"]),
        "topic": str(video_dict["topic"]),
        "title": video_dict.get("title"),
        "channel": video_dict.get("channel"),
        "description": video_dict.get("description"),
        "views": int(video_dict.get("views") or 0),
        "duration_sec": int(video_dict.get("duration_sec") or 0),
        "thumbnail": video_dict.get("thumbnail"),
        "label": video_dict.get("label"),
    }

    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO videos (
                video_id, topic, title, channel, description, views, duration_sec, thumbnail, label
            ) VALUES (
                :video_id, :topic, :title, :channel, :description, :views, :duration_sec, :thumbnail, :label
            )
            ON CONFLICT(video_id) DO UPDATE SET
                topic = excluded.topic,
                title = excluded.title,
                channel = excluded.channel,
                description = excluded.description,
                views = excluded.views,
                duration_sec = excluded.duration_sec,
                thumbnail = excluded.thumbnail,
                label = COALESCE(excluded.label, videos.label)
            """,
            params,
        )
        conn.commit()


def update_label(video_id: str, label: Optional[int], db_path: str = DB_PATH) -> None:
    """Updates the label of a video (1 = Like, 0 = Dislike, None = Unrated)."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE videos SET label = ? WHERE video_id = ?",
            (label, video_id),
        )
        conn.commit()


def get_videos_by_topic(topic: str, db_path: str = DB_PATH) -> List[Dict[str, Any]]:
    """Retrieves all videos belonging to a specific topic."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT * FROM videos WHERE topic = ? ORDER BY rowid DESC",
            (topic,),
        )
        rows = cursor.fetchall()
        return [dict(row) for row in rows]


def update_video_topic(video_id: str, new_topic: str, db_path: str = DB_PATH) -> None:
    """Updates the topic of an existing video in videos.db."""
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE videos SET topic = ? WHERE video_id = ?",
            (new_topic, video_id),
        )
        conn.commit()


def delete_demo_videos(video_ids: Optional[List[str]] = None, db_path: str = DB_PATH) -> int:
    """Deletes seeded demo videos from videos.db, returning the count of deleted records."""
    if video_ids is None:
        video_ids = [
            "7xTGNNLPyMI", "aircAruvnKk", "r5Y1I1Zol2k", "b3NxrZO8d9o",
            "VyWAvY2CF9c", "zjkBMFhNj_g", "kJQP7kiw5Fk", "9bZkp7q19f0"
        ]
    with get_connection(db_path) as conn:
        cursor = conn.cursor()
        placeholders = ",".join("?" for _ in video_ids)
        cursor.execute(
            f"DELETE FROM videos WHERE video_id IN ({placeholders}) OR video_id LIKE 'tech_%' OR video_id LIKE 'ent_%'",
            video_ids,
        )
        conn.commit()
        return cursor.rowcount


if __name__ == "__main__":
    init_db()
    with get_connection(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(videos)")
        columns = cursor.fetchall()
        print(f"Database successfully initialized at: {DB_PATH}")
        print("Table 'videos' schema:")
        for col in columns:
            print(f" - {col['name']} ({col['type']}), NotNull: {col['notnull']}, Dflt: {col['dflt_value']}, PK: {col['pk']}")
