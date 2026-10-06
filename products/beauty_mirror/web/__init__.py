"""妆镜 Web 原型：传图页 → 报告页 → 跟练页。

入口：`python -m beauty_mirror.web.server`
"""

from .plan import build_plan, plan_text, tutorial_fallback  # noqa: F401
from .sessions import Session, SessionStore  # noqa: F401

__all__ = ["build_plan", "plan_text", "tutorial_fallback", "Session", "SessionStore"]
