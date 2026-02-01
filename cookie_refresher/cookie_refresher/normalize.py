# cookie_refresher/cookie_refresher/normalize.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlparse


COMMON_STRIP_PREFIXES = (
    "www.",
    "m.",
    "mobile.",
    "old.",
    "new.",
    "amp.",
)


@dataclass(frozen=True)
class CookieNormalizeConfig:
    """
    Конфиг нормализации.
    - strip_common_prefixes: убирать ли www/m/mobile/old/new/amp у домена
    - keep_leading_dot: возвращать домен в виде ".example.com" (Netscape формат обычно ок с точкой)
    - aggressive: если True — сильнее канонизируем (осторожно, может ломать редкие кейсы)
    """
    strip_common_prefixes: bool = True
    keep_leading_dot: bool = True
    aggressive: bool = False


def _clean_domain(domain: str) -> str:
    d = (domain or "").strip()
    if not d:
        return ""
    # selenium иногда хранит с ведущей точкой
    d = d.lstrip(".")
    # нижний регистр для стабильности
    d = d.lower()
    # иногда прилетает с хвостовой точкой
    d = d.rstrip(".")
    return d


def _get_host_from_url(url_hint: Optional[str]) -> str:
    if not url_hint:
        return ""
    try:
        host = urlparse(url_hint).hostname or ""
        return host.lower()
    except Exception:
        return ""


def _strip_common_prefixes(domain: str) -> str:
    d = domain
    changed = True
    # снимаем несколько префиксов подряд: например "www.m.site.com"
    while changed:
        changed = False
        for p in COMMON_STRIP_PREFIXES:
            if d.startswith(p) and len(d) > len(p) + 1:
                d = d[len(p):]
                changed = True
    return d


def _canonicalize_by_rules(domain: str, host_hint: str, aggressive: bool) -> str:
    """
    Специальные правила для крупных платформ.
    Это целенаправленные фиксы (как твой `.www.reddit`).
    """
    d = domain

    # ---------- REDDIT ----------
    # .www.reddit.com -> reddit.com
    if d.endswith("www.reddit.com"):
        d = d.replace("www.reddit.com", "reddit.com")

    # ---------- YOUTUBE / GOOGLE ----------
    # music.youtube.com / m.youtube.com -> youtube.com (если aggressive)
    if aggressive and (d.endswith("music.youtube.com") or d.endswith("m.youtube.com")):
        d = "youtube.com"
    # youtu.be cookies обычно не нужны — но если прилетело, приводим к youtube.com (aggressive)
    if aggressive and d.endswith("youtu.be"):
        d = "youtube.com"

    # ---------- TIKTOK ----------
    # m.tiktok.com / www.tiktok.com -> tiktok.com
    if d.endswith("m.tiktok.com") or d.endswith("www.tiktok.com"):
        d = "tiktok.com"
    # vm.tiktok.com / vt.tiktok.com — обычно редирект-сабдомены, cookie на них часто бессмысленны
    if aggressive and (d.endswith("vm.tiktok.com") or d.endswith("vt.tiktok.com")):
        d = "tiktok.com"

    # ---------- TWITTER / X ----------
    # Сейчас часто встречается x.com и twitter.com параллельно.
    # Безопаснее НЕ смешивать принудительно всегда.
    # Но можно канонизировать сабдомены типа mobile.twitter.com -> twitter.com
    if d.endswith("mobile.twitter.com") or d.endswith("m.twitter.com"):
        d = "twitter.com"

    if d.endswith("mobile.x.com") or d.endswith("m.x.com"):
        d = "x.com"

    # Если у тебя в пайплайне точно используется twitter.com (а не x.com),
    # то можешь включить агрессивный мост x.com -> twitter.com или наоборот.
    if aggressive:
        # пример: всё к x.com
        if d == "twitter.com":
            # оставляем как есть; менять лучше только если ты точно знаешь, куда ходишь
            pass

    # ---------- INSTAGRAM / META ----------
    if aggressive and (d.endswith("m.instagram.com") or d.endswith("www.instagram.com")):
        d = "instagram.com"

    # ---------- FACEBOOK ----------
    if aggressive and (d.endswith("m.facebook.com") or d.endswith("www.facebook.com")):
        d = "facebook.com"

    # ---------- REDIRECTORS / SHORTENERS ----------
    # t.co, bit.ly, etc — cookies там почти никогда не нужны (но мы их не трогаем, просто оставляем)
    # тут ничего

    # ---------- Host-hint based tweaks ----------
    # Если domain пустой/кривой, но есть host_hint — используем host_hint
    if not d and host_hint:
        d = _clean_domain(host_hint)

    return d


def normalize_cookie_domain(
    domain: str,
    *,
    url_hint: Optional[str] = None,
    cfg: Optional[CookieNormalizeConfig] = None,
) -> str:
    """
    Возвращает домен cookie в нормализованном виде.
    По умолчанию:
      - чистим точки/регистр
      - убираем www/m/mobile/old/new/amp
      - применяем спец-правила (reddit/tiktok/twitter/youtube...)
      - возвращаем с ведущей точкой (удобно для Netscape)
    """
    cfg = cfg or CookieNormalizeConfig()
    host_hint = _get_host_from_url(url_hint)

    d = _clean_domain(domain)

    # Если домен пуст, попробуем по url_hint
    if not d and host_hint:
        d = _clean_domain(host_hint)

    if cfg.strip_common_prefixes:
        d = _strip_common_prefixes(d)

    d = _canonicalize_by_rules(d, host_hint, cfg.aggressive)

    # Ещё раз уберём префиксы после спец-правил (на всякий)
    if cfg.strip_common_prefixes:
        d = _strip_common_prefixes(d)

    # Возвращаем в виде ".domain" (как обычно ожидается в Netscape cookie file)
    if cfg.keep_leading_dot and d and not d.startswith("."):
        d = "." + d

    return d


def normalize_cookie_dict(
    cookie: Dict[str, Any],
    *,
    url_hint: Optional[str] = None,
    cfg: Optional[CookieNormalizeConfig] = None,
) -> Dict[str, Any]:
    """
    Нормализует cookie dict (Selenium-like).
    Возвращает новый dict (не мутирует исходный).
    """
    cfg = cfg or CookieNormalizeConfig()

    c = dict(cookie)  # копия
    if "domain" in c:
        c["domain"] = normalize_cookie_domain(
            c.get("domain", ""),
            url_hint=url_hint,
            cfg=cfg,
        )
        # Selenium чаще ожидает домен без ведущей точки при add_cookie,
        # но у тебя это уже отфильтровывается/обрабатывается.
        # Если нужно для add_cookie: можно хранить без точки.
    return c
