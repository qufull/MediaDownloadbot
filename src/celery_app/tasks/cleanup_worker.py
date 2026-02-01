import logging
from pathlib import Path
from typing import List, Tuple
from datetime import datetime, timedelta

from ..app import celery_app

from src.config import settings


logger = logging.getLogger(name=__name__)


def get_folder_size(folder_path: Path) -> Tuple[int, int]:
    """
    Получить размер папки и количество файлов.
    
    Args:
        folder_path: Путь к папке
        
    Returns:
        Tuple[размер_в_байтах, количество_файлов]
    """
    total_size = 0
    file_count = 0
    
    logger.debug(f"Начало расчета размера папки: {folder_path}")
    
    for file_path in folder_path.rglob("*"):
        if file_path.is_file():
            try:
                file_size = file_path.stat().st_size
                total_size += file_size
                file_count += 1
                logger.debug(f"Файл: {file_path.name}, размер: {file_size} байт")
            except OSError as e:
                logger.warning(f"Не удалось получить размер файла {file_path}: {e}")
                continue
    
    logger.info(f"Размер папки {folder_path}: {total_size} байт, файлов: {file_count}")
    return total_size, file_count


def get_oldest_files(folder_path: Path, limit: int = None) -> List[Tuple[Path, datetime, int]]:
    """
    Получить список файлов отсортированных по дате изменения (от старых к новым).
    
    Args:
        folder_path: Путь к папке
        limit: Ограничение количества файлов (None - все файлы)
        
    Returns:
        List[Tuple[путь_к_файлу, дата_изменения, размер]]
    """
    files = []
    
    logger.debug(f"Поиск файлов в папке: {folder_path}")
    
    for file_path in folder_path.rglob("*"):
        if file_path.is_file():
            try:
                stat = file_path.stat()
                modify_time = datetime.fromtimestamp(stat.st_mtime)
                files.append((file_path, modify_time, stat.st_size))
                logger.debug(f"Найден файл: {file_path.name}, дата: {modify_time}, размер: {stat.st_size} байт")
            except OSError as e:
                logger.warning(f"Не удалось получить информацию о файле {file_path}: {e}")
                continue
    
    # Сортируем по дате изменения (старые сначала)
    files.sort(key=lambda x: x[1])
    
    if limit:
        files = files[:limit]
        logger.debug(f"Ограничение списка файлов до {limit} элементов")
    
    logger.info(f"Найдено файлов для сортировки: {len(files)}")
    return files


def smart_cleanup(
    folder_path: Path, 
    max_size_gb: float = 30.0,
    max_age_hours: int = 24,
    target_free_percent: float = 65.0
) -> dict:
    """
    Умная очистка папки: удаляет файлы если превышен лимит размера или возраста.
    
    Args:
        folder_path: Путь к папке для очистки
        max_size_gb: Максимальный размер папки в GB
        max_age_hours: Максимальный возраст файлов в часах
        target_free_percent: Целевой процент свободного места (до скольки % очищать)
        
    Returns:
        dict: Статистика очистки
    """
    logger.info(f"Запуск умной очистки папки: {folder_path}")
    logger.info(f"Параметры: max_size_gb={max_size_gb}, max_age_hours={max_age_hours}, target_free_percent={target_free_percent}")
    
    if not folder_path.exists():
        logger.error(f"Папка не существует: {folder_path}")
        return {"status": "error", "message": "Папка не существует"}
    
    max_size_bytes = max_size_gb * 1024 * 1024 * 1024
    current_size, file_count = get_folder_size(folder_path)
    current_size_gb = current_size / 1024 / 1024 / 1024
    
    logger.info(f"Текущий размер папки: {current_size_gb:.2f} GB / {max_size_gb} GB, файлов: {file_count}")
    
    # Получаем все файлы отсортированные по возрасту (старые сначала)
    all_files = get_oldest_files(folder_path)
    
    deleted_files = 0
    deleted_size = 0
    deleted_by_age = 0
    deleted_by_size = 0
    
    # 1. Удаляем файлы старше max_age_hours
    cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
    logger.info(f"Удаление файлов старше: {cutoff_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    for file_path, modify_time, file_size in all_files[:]:  # Копируем список для безопасного удаления
        if modify_time < cutoff_time:
            try:
                file_path.unlink()
                deleted_files += 1
                deleted_size += file_size
                deleted_by_age += 1
                current_size -= file_size
                all_files.remove((file_path, modify_time, file_size))  # Удаляем из списка
                logger.info(f"Удален старый файл ({modify_time.strftime('%Y-%m-%d %H:%M')}): {file_path.name}, размер: {file_size} байт")
            except Exception as e:
                logger.error(f"Ошибка удаления файла {file_path}: {e}")
    
    if deleted_by_age > 0:
        logger.info(f"Удалено файлов по возрасту: {deleted_by_age}, освобождено: {deleted_size / 1024 / 1024:.2f} MB")
    
    # 2. Если после удаления старых файлов все еще превышен лимит - удаляем до target_free_percent
    if current_size > max_size_bytes:
        target_size = max_size_bytes * (target_free_percent / 100.0)
        size_to_delete = current_size - target_size
        
        logger.warning(f"Превышен лимит размера папки. Требуется удалить: {size_to_delete / 1024 / 1024 / 1024:.2f} GB")
        logger.info(f"Целевой размер после очистки: {target_size / 1024 / 1024 / 1024:.2f} GB")
        
        if size_to_delete > 0:
            accumulated_size = 0
            
            for file_path, modify_time, file_size in all_files:
                if accumulated_size >= size_to_delete:
                    break
                    
                try:
                    file_path.unlink()
                    deleted_files += 1
                    deleted_size += file_size
                    deleted_by_size += 1
                    accumulated_size += file_size
                    logger.info(f"Удален для освобождения места ({modify_time.strftime('%Y-%m-%d %H:%M')}): {file_path.name}, размер: {file_size} байт")
                except Exception as e:
                    logger.error(f"Ошибка удаления файла {file_path}: {e}")
            
            if deleted_by_size > 0:
                logger.info(f"Удалено файлов по размеру: {deleted_by_size}, освобождено: {accumulated_size / 1024 / 1024:.2f} MB")
    else:
        logger.info("Лимит размера папки не превышен, очистка по размеру не требуется")
    
    # Получаем финальную статистику
    final_size, final_file_count = get_folder_size(folder_path)
    final_size_gb = final_size / 1024 / 1024 / 1024
    
    logger.info(f"Финальная статистика очистки:")
    logger.info(f"  Всего удалено файлов: {deleted_files}")
    logger.info(f"  Удалено по возрасту: {deleted_by_age}")
    logger.info(f"  Удалено по размеру: {deleted_by_size}")
    logger.info(f"  Освобождено места: {deleted_size / 1024 / 1024:.2f} MB")
    logger.info(f"  Текущий размер папки: {final_size_gb:.2f} GB")
    logger.info(f"  Файлов осталось: {final_file_count}")
    logger.info(f"  Заполнение папки: {(final_size_gb / max_size_gb * 100):.1f}%")
    
    return {
        "status": "success",
        "deleted_files": deleted_files,
        "deleted_size_mb": round(deleted_size / 1024 / 1024, 2),
        "deleted_by_age": deleted_by_age,
        "deleted_by_size": deleted_by_size,
        "current_size_mb": round(final_size / 1024 / 1024, 2),
        "current_file_count": final_file_count,
        "folder_usage_percent": round((final_size / max_size_bytes) * 100, 1) if max_size_bytes > 0 else 0
    }


@celery_app.task(name="smart_cleanup_downloads")
def smart_cleanup_downloads():
    """
    Задача Celery для умной очистки папки downloads.
    """
    logger.info("🚀 Запуск задачи Celery: smart_cleanup_downloads")
    
    downloads_path = settings.local_storage.media_storage_path
    
    try:
        result = smart_cleanup(
            folder_path=downloads_path,
        )
        
        # Логируем результат
        if result["status"] == "success":
            logger.info("✅ Умная очистка завершена успешно")
            logger.info(f"   📁 Удалено файлов: {result['deleted_files']}")
            logger.info(f"   🗑️  По возрасту: {result['deleted_by_age']}")
            logger.info(f"   💾 По размеру: {result['deleted_by_size']}")
            logger.info(f"   📊 Освобождено: {result['deleted_size_mb']} MB")
            logger.info(f"   📈 Текущий размер: {result['current_size_mb']} MB")
            logger.info(f"   📋 Файлов осталось: {result['current_file_count']}")
            logger.info(f"   ⚡ Заполнение: {result['folder_usage_percent']}%")
        else:
            logger.error(f"❌ Ошибка очистки: {result['message']}")
        
        return result
        
    except Exception as e:
        logger.error(f"💥 Критическая ошибка в задаче очистки: {e}", exc_info=True)
        return {
            "status": "error", 
            "message": f"Критическая ошибка: {str(e)}"
        }


@celery_app.task(name="quick_cleanup_old_files")
def quick_cleanup_old_files(max_age_hours: int = 24):
    """
    Быстрая очистка только по возрасту файлов.
    """
    logger.info(f"🚀 Запуск задачи Celery: quick_cleanup_old_files (max_age_hours={max_age_hours})")
    
    downloads_path = settings.local_storage.media_storage_path
    
    if not downloads_path.exists():
        logger.error("Папка downloads не существует")
        return {"status": "error", "message": "Папка downloads не существует"}
    
    cutoff_time = datetime.now() - timedelta(hours=max_age_hours)
    logger.info(f"Удаление файлов старше: {cutoff_time.strftime('%Y-%m-%d %H:%M:%S')}")
    
    deleted_files = 0
    deleted_size = 0
    
    try:
        for file_path in downloads_path.rglob("*"):
            if file_path.is_file():
                try:
                    file_time = datetime.fromtimestamp(file_path.stat().st_mtime)
                    if file_time < cutoff_time:
                        file_size = file_path.stat().st_size
                        file_path.unlink()
                        deleted_files += 1
                        deleted_size += file_size
                        logger.info(f"Удален старый файл ({file_time.strftime('%Y-%m-%d %H:%M')}): {file_path.name}")
                except Exception as e:
                    logger.error(f"Ошибка удаления файла {file_path}: {e}")
        
        current_size, current_count = get_folder_size(downloads_path)
        
        logger.info(f"✅ Быстрая очистка завершена:")
        logger.info(f"   Удалено файлов: {deleted_files}")
        logger.info(f"   Освобождено места: {deleted_size / 1024 / 1024:.2f} MB")
        logger.info(f"   Текущий размер: {current_size / 1024 / 1024:.2f} MB")
        logger.info(f"   Файлов осталось: {current_count}")
        
        return {
            "status": "success",
            "deleted_files": deleted_files,
            "deleted_size_mb": round(deleted_size / 1024 / 1024, 2),
            "current_size_mb": round(current_size / 1024 / 1024, 2),
            "current_file_count": current_count
        }
        
    except Exception as e:
        logger.error(f"💥 Критическая ошибка в быстрой очистке: {e}", exc_info=True)
        return {
            "status": "error",
            "message": f"Критическая ошибка: {str(e)}"
        }
