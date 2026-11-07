"""langdetect: probabilistic natural-language detection with its bundled profiles."""

from __future__ import annotations

from typing import TYPE_CHECKING, Final

from langdetect import DetectorFactory
from langdetect.detector_factory import PROFILES_DIRECTORY

if TYPE_CHECKING:
    from langdetect.language import Language

REQUIREMENTS: Final[tuple[str, ...]] = ("langdetect>=1.0.9",)

_FACTORY: Final[DetectorFactory] = DetectorFactory()
_FACTORY.load_profile(PROFILES_DIRECTORY)
_FACTORY.set_seed(0)


def _detect_language(text: str) -> list[Language]:
    detector: Final = _FACTORY.create()
    detector.set_max_text_length(len(text))
    detector.append(text)
    return detector.get_probabilities()


OPERATIONS = {
    "detect-language": (_detect_language, "langdetect"),
    "detect-language-long": (_detect_language, "langdetect"),
}

__all__ = []
