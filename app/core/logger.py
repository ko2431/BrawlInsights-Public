import logging

logger = logging.getLogger("brawl_insights")


def describe_exception(exc: BaseException) -> str:
    """例外を短い文字列で表す。

    TimeoutError などメッセージが空の例外は str(e) が空になるため、原因（__cause__ / __context__）を
    たどって最初に見つかったメッセージを使い、それも無ければ一番内側の例外の型名にする。
    """
    current: BaseException | None = exc
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        text = str(current).strip()
        if text:
            return text if current is exc else f"{type(current).__name__}: {text}"
        inner = current.__cause__ or current.__context__
        if inner is None:
            return type(current).__name__
        current = inner
    return type(exc).__name__
