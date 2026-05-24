from celery import Celery
import asyncio
import threading
from celery.schedules import crontab

from src.config import settings

shared_loop = asyncio.new_event_loop()

def _start_background_loop(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()

# 2. Запускаем этот цикл в отдельном фоновом потоке навсегда
loop_thread = threading.Thread(target=_start_background_loop, args=(shared_loop,), daemon=True)
loop_thread.start()

celery_app = Celery(
    "src.celery_app.tasks.app",
    broker=f"redis://{settings.redis.host}:{settings.redis.port}/{settings.redis.broker_db}",
    backend=f"redis://{settings.redis.host}:{settings.redis.port}/{settings.redis.backend_db}",
)

celery_app.conf.update(
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_track_started=True,

    # лимиты, чтобы не висеть вечно (подстрой под свою реальность)
    task_soft_time_limit=270,  # 20 минут
    task_time_limit=300,
    worker_max_tasks_per_child=50,

    task_routes={
        # Очередь для извлечения информации
        "src.celery_app.tasks.media_extractor_worker.extract_info": {"queue": "extract_queue"},
        # Очередь для загрузки аудио
        "src.celery_app.tasks.audio_download_worker.download_audio": {"queue": "download_audio_queue"},
        # Очереди для загрузки видео
        "src.celery_app.tasks.video_download_worker.download_youtube_video": {"queue": "download_youtube_queue"},
        "src.celery_app.tasks.video_download_worker.download_rutube_video": {"queue": "download_rutube_queue"},
        "src.celery_app.tasks.video_download_worker.download_reddit_video": {"queue": "download_reddit_queue"},
        "src.celery_app.tasks.video_download_worker.download_tiktok_video": {"queue": "download_tiktok_queue"},
        "src.celery_app.tasks.video_download_worker.download_twitter_video": {"queue": "download_twitter_queue"},
        "src.celery_app.tasks.video_download_worker.download_instagram_video": {"queue": "download_instagram_queue"},
        "src.celery_app.tasks.video_download_worker.download_vk_video": {"queue": "download_vk_queue"},
        "src.celery_app.tasks.video_download_worker.download_pinterest_video": {"queue": "download_pinterest_queue"},

        # Очереди для очистки папки
        "src.celery_app.tasks.cleanup_worker.smart_cleanup_downloads": {"queue": "cleanup_queue"},
        "src.celery_app.tasks.cleanup_worker.quick_cleanup_old_files": {"queue": "cleanup_queue"},
    },

    broker_connection_retry_on_startup=True,
    broker_connection_retry=True,

    beat_schedule={
        # Умная очистка каждый час
        'smart-cleanup-every-hour': {
            'task': 'smart_cleanup_downloads',
            'schedule': crontab(minute=0),  # Каждый час в 0 минут
            'args': (),
            'options': {'queue': 'cleanup_queue'}
        },

        # Быстрая очистка каждые 6 часов
        'quick-cleanup-every-6-hours': {
            'task': 'quick_cleanup_old_files',
            'schedule': crontab(minute=0, hour='*/6'),  # Каждые 6 часов в 0 минут
            'args': (24,),  # Удалять файлы старше 24 часов
            'options': {'queue': 'cleanup_queue'}
        },

        # Дополнительная агрессивная очистка ночью
        'nightly-deep-cleanup': {
            'task': 'quick_cleanup_old_files',
            'schedule': crontab(hour=3, minute=0),  # Каждый день в 3:00
            'args': (12,),  # Удалять файлы старше 12 часов
            'options': {'queue': 'cleanup_queue'}
        },
    },
    task_annotations={
        "src.celery_app.tasks.video_download_worker.download_youtube_video": {
            "rate_limit": "12/m"
        }
    }
)

celery_app.autodiscover_tasks(
    [
        "src.celery_app.tasks.media_extractor_worker",
        "src.celery_app.tasks.audio_download_worker",
        "src.celery_app.tasks.video_download_worker",
        "src.celery_app.tasks.cleanup_worker",
    ],
    force=True
)

if __name__ == "__main__":
    celery_app.start()