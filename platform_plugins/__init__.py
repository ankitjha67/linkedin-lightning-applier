"""
Multi-Platform Job Application Plugins.

Abstracts the browser interaction layer so each platform is a plugin.
LinkedIn first, then Indeed, Glassdoor, Naukri, AngelList.
"""

from .base import JobPlatform

__all__ = ["JobPlatform"]
