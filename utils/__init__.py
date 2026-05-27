"""Utility modules for DeepSeek AI trading strategy."""

__all__ = ["DeepSeekAnalyzer", "SentimentDataFetcher"]


def __getattr__(name):
    """Lazily import heavier optional utility dependencies."""
    if name == "DeepSeekAnalyzer":
        from .deepseek_client import DeepSeekAnalyzer

        return DeepSeekAnalyzer
    if name == "SentimentDataFetcher":
        from .sentiment_client import SentimentDataFetcher

        return SentimentDataFetcher
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
