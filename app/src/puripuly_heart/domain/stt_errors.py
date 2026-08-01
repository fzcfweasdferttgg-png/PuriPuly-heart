from __future__ import annotations


class LocalQwenSherpaLoadError(RuntimeError):
    """Raised when the local sherpa recognizer cannot be initialized."""


class LocalGigaamRnntLoadError(RuntimeError):
    """Raised when the local GigaAM RNNT model cannot be loaded."""


class LocalParakeetTdtLoadError(RuntimeError):
    """Raised when the local Parakeet TDT model cannot be loaded."""


class LocalParakeetCtcLoadError(RuntimeError):
    """Raised when the local Parakeet CTC model cannot be loaded."""


class LocalTranscribecppLoadError(RuntimeError):
    """Raised when the local transcribecpp model cannot be loaded."""
