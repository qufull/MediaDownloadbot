from typing import List, Literal, Optional, TypedDict, NotRequired


class ImageDictAnnotation(TypedDict):
    id: str
    url: str
    name: str
    width: NotRequired[Optional[int]]
    height: NotRequired[Optional[int]]


class VideoDictAnnotation(TypedDict):
    id: str
    url: str
    name: str
    has_audio: bool
    fps: NotRequired[Optional[int]]
    width: NotRequired[Optional[int]]
    height: NotRequired[Optional[int]]
    language: NotRequired[Optional[str]]
    total_bitrate: NotRequired[Optional[int]]
    language_preference: NotRequired[Optional[int]]


class AudioDictAnnotation(TypedDict):
    id: str
    url: str
    name: str
    author: NotRequired[Optional[str]]
    language: NotRequired[Optional[str]]
    total_bitrate: NotRequired[Optional[int]]
    language_preference: NotRequired[Optional[int]]


class DataDictAnnotation(TypedDict):
    url: str
    is_video: bool
    is_image: bool
    path: NotRequired[Optional[str]]
    title: NotRequired[Optional[str]]
    author_name: NotRequired[Optional[str]]
    description: NotRequired[Optional[str]]
    videos: List[VideoDictAnnotation]
    images: List[ImageDictAnnotation]
    audios: List[AudioDictAnnotation]
    thumbnails: List[ImageDictAnnotation]


class ResultDictAnnotation(TypedDict):
    status: Literal["success", "error"]
    context: NotRequired[Optional[str]]
    code: str
    data: NotRequired[Optional[DataDictAnnotation]]
