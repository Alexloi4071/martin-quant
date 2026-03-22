"""scanner package exports."""
from .daily_scan_v2 import DailyScannerV2, ScanV2Result

# Backward-compatible alias.
ScanResultV2 = ScanV2Result

__all__ = ["DailyScannerV2", "ScanV2Result", "ScanResultV2"]
