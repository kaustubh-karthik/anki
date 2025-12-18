from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Topic:
    id: str
    title_en: str
    summary_en: str


DEFAULT_TOPICS: tuple[Topic, ...] = (
    Topic(id="room_objects", title_en="Room Objects", summary_en="Finding objects in a room."),
    Topic(id="food_ordering", title_en="Food Ordering", summary_en="Ordering food and drinks politely."),
    Topic(id="campus_life", title_en="Campus Life", summary_en="Talking about classes and schedules."),
)


def get_topic(topic_id: str) -> Topic | None:
    for t in DEFAULT_TOPICS:
        if t.id == topic_id:
            return t
    return None

